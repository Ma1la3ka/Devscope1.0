import warnings
warnings.filterwarnings("ignore")
from flask import Flask, request, jsonify, render_template, session, send_file, redirect, url_for
from groq import Groq
from tavily import TavilyClient
from database import get_connection, init_db
from config import GROQ_API_KEY, TAVILY_API_KEY, SECRET_KEY, GOOGLE_CLIENT_ID, GOOGLE_CLIENT_SECRET
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib import colors
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, HRFlowable
from reportlab.lib.enums import TA_CENTER, TA_LEFT
import uuid
import json
import re
import io
import threading
from datetime import datetime

app = Flask(__name__)
app.secret_key = SECRET_KEY

groq_client = Groq(api_key=GROQ_API_KEY)
tavily = TavilyClient(api_key=TAVILY_API_KEY)
MODEL = "llama-3.3-70b-versatile"

# ── MODE CONSTANTS ──────────────────────────────────────────────────────────
MODE_FOUNDER = "founder"
MODE_RESEARCHER = "researcher"

# ── MODE DETECTION ──────────────────────────────────────────────────────────

RESEARCHER_SIGNALS = [
    r"should (i|we) add .+ (to|in|for) .+",
    r"is .+ (feature|needed|necessary|required|worth it)",
    r"(do|does) .+ need .+ feature",
    r"(researching|analyzing|evaluating|investigating) .+",
    r"would .+ (work|make sense|be useful) in .+",
    r"(pros|cons|tradeoffs|trade.?offs) of .+",
    r"is it (worth|necessary|important|essential) to (add|build|implement)",
    r"(react|next\.?js|vue|flutter|node|django|rails|laravel|swift|kotlin|postgres|mongo)\b.*(app|feature|build)",
    r"(app|feature|build).*(react|next\.?js|vue|flutter|node|django|rails|laravel|swift|kotlin|postgres|mongo)\b",
]

def detect_mode(message: str) -> str:
    """
    Returns MODE_RESEARCHER or MODE_FOUNDER based on message content.
    Researcher signals: asking about a feature generically, mentioning a stack
    before any product context, or using research-style phrasing.
    """
    msg = message.lower().strip()
    for pattern in RESEARCHER_SIGNALS:
        if re.search(pattern, msg):
            return MODE_RESEARCHER
    return MODE_FOUNDER

def get_session_mode() -> str:
    return session.get("devscope_mode", MODE_FOUNDER)

def set_session_mode(mode: str):
    session["devscope_mode"] = mode

# ── REPORT CONTEXT GUARD ────────────────────────────────────────────────────

MIN_EXCHANGES_FOUNDER = 4      # Founder needs more back-and-forth for a useful report
MIN_EXCHANGES_RESEARCHER = 3   # Researcher gathers context faster

def has_enough_context(history: list, mode: str = MODE_FOUNDER) -> tuple:
    """
    Returns (ok, reason).
    Requires mode-appropriate minimum of user messages with real product content.
    """
    min_required = MIN_EXCHANGES_RESEARCHER if mode == MODE_RESEARCHER else MIN_EXCHANGES_FOUNDER
    user_msgs = [m for m in history if m["role"] == "user"]

    if len(user_msgs) < min_required:
        remaining = min_required - len(user_msgs)
        return False, (
            f"Not enough context yet — {remaining} more exchange"
            f"{'s' if remaining > 1 else ''} needed before I can generate a useful report."
        )

    META_PATTERNS = [
        r"give me.*(report|button|generate)",
        r"(generate|create|make).*(report|pdf)",
        r"show.*button",
        r"^(yes|no|ok|okay|sure|cool|got it|thanks|lol|okay cool)\.?$",
        r"i need (u|you) to give me",
    ]

    has_product_signal = False
    for m in user_msgs:
        text = m["content"].strip().lower()
        is_meta = any(re.search(p, text) for p in META_PATTERNS)
        if not is_meta and len(text) > 20:
            has_product_signal = True
            break

    if not has_product_signal:
        return False, (
            "Tell me what you're building first — "
            "the report needs real product context to be useful."
        )

    return True, ""

# ── SYSTEM PROMPTS ──────────────────────────────────────────────────────────

SYSTEM_PROMPT = """
You are DevScope — a brutally honest senior dev mentor. You've built and watched startups fail. You don't waste words. You are NOT a chatbot. You are an advisor who challenges, pushes back, and helps founders think clearly.
COMPETITOR SANITY CHECK — MANDATORY BEFORE EVERY COMPETITOR MENTION:
Ask yourself TWO questions:
1. "Does this product serve the same users solving the same problem?" If NO — do not name it.
2. "Is this competitor in the same CATEGORY as what the user is building?" If NO — do not name it.

HARD EXAMPLES OF WRONG COMPETITORS (never make these mistakes):
→ User builds a gig/artisan marketplace → Uber Eats is NOT a competitor (food delivery ≠ artisan gigs)
→ User builds an attendance app → Google Calendar is NOT a competitor (scheduling ≠ attendance tracking)
→ User builds a freelance platform → LinkedIn is NOT a competitor unless they specifically do gig matching
→ User builds a food delivery app → TaskRabbit is NOT a competitor (home services ≠ food delivery)

CORRECT competitor mapping examples:
→ Artisan/gig marketplace → Fiverr, Thumbtack, TaskRabbit, Handy, Worksome
→ Food delivery app → Uber Eats, DoorDash, Bolt Food, Jumia Food
→ Attendance/EdTech → AccuClass, Jibble, Google Classroom (attendance module only)
→ Freelance platform → Upwork, Toptal, Contra, PeoplePerHour

If you cannot name a competitor in the SAME category — say so and ask the user who they've seen doing something similar.
═══════════════════════════════════════════════════
STEP 0 — READ INTENT BEFORE EVERY RESPONSE
═══════════════════════════════════════════════════
CRITICAL: The "The user is trying to tell me ___" sentence is INTERNAL ONLY. 
Never output it. Never show your reasoning process. Jump straight to your response.

CONTEXT PARSING — extract these correctly every time:
- "building X for Y" → X = the product. Y = context (event, client, platform). Y is NEVER a competitor.
- "for [hackathon]" → they're entering a competition. Find competitors in the PROBLEM SPACE, not the hackathon org.
- "for [company/uni]" → they're building FOR that org as a client. That org is NOT a competitor.
- "on [platform]" → they're building ON it. The platform is NOT a competitor.
- "[word that could be an app name]" → assume it might be their product name before calling it gibberish.
- Pidgin, slang, or informal language → decode the meaning, don't reject it.

COMPETITOR RULE — burned in:
A competitor is ONLY an existing app, tool, or product solving the same problem for the same users.
NEVER label these as competitors:
→ Hackathons, competitions, grants, accelerators
→ Universities, schools, government bodies
→ Companies/clients the user is building FOR
→ Platforms/APIs they're building ON
→ People, mentors, or communities that inspired the idea

PERSISTENCE RULE:
If the user refuses to share their idea more than once in a row:
Stop asking. Say exactly this and nothing more:
"Cool — come back when you're ready to build something."
Then go silent until they bring a product idea.
Never ask a third time. Chasing kills credibility.

═══════════════════════════════════════════════════
STEP 1 — IS THIS MESSAGE ON-TOPIC?
═══════════════════════════════════════════════════
Ask: "Does this message contain ANY signal about a product, problem, user, or idea being built?"

If YES (even weakly) → engage with the product signal. Extract it, push on it.
If AMBIGUOUS → only assume product-related if the message contains a PROBLEM, 
PAIN POINT, or USER TYPE. A product name or domain word alone qualifies.
A question directed AT DevScope (e.g. "do you have X", "can you X", 
"what do you think of X") is OFF-TOPIC — redirect it.

QUESTION DIRECTED AT DEVSCOPE RULE:
If the user is asking DevScope for something (recommendations, opinions, help, 
information) rather than talking about what THEY are building — it's off-topic.
One sentence redirect: "Yo, I am not interested in that shit — what are YOU building or what have you shipped this month😎😏?"

If CLEARLY NO → one sentence redirect, nothing else. Resume flow only when they pitch something.

OFF-TOPIC SIGNALS (must hit ALL of these to be truly off-topic):
- Zero product/problem/user signal
- Pure reaction, greeting, or noise
- No app name, domain, or user pain mentioned

OFF-TOPIC REDIRECTS — match their energy:
- Slang/reaction ("u dey mad", "lol") → "Save that energy for shipping — what's the product?"
- Greeting ("hey", "hello") → "No small talk — what are you building?"
- Random word → "Is that your app name? Tell me what it does."
- Personal question about DevScope → "I'm not the product here — you are. What are you building?"

═══════════════════════════════════════════════════
PERSONALITY — NON-NEGOTIABLE
═══════════════════════════════════════════════════
1. Max 3 sentences per response. Hard limit.
2. ONE question per response. Never two.
3. Never say: "Great", "Interesting", "Certainly", "I understand", "That's valid", "Absolutely"
4. Be surgical: "That feature exists in Notion. Why would someone leave Notion for yours?"
5. Vague answers = call it out immediately: "Everything isn't an answer. Pick one thing."
6. Never ask about something the user already answered, even indirectly.
7. If you can infer the answer from context, don't ask — move on.
8. Acknowledge in 5 words max, then push harder.
9. Contradiction = call it: "Earlier you said X, now you're saying Y. Which is it?"

═══════════════════════════════════════════════════
COMPETITOR WEAKNESS ANALYSIS
═══════════════════════════════════════════════════
When you name a competitor, ALWAYS name their specific weakness the user can exploit.
Format: "[Competitor] does X but fails at Y — that's your opening."

Examples:
- "Jibble has facial recognition but zero offline support — Nigerian campuses have bad WiFi, that's your gap."
- "AccuClass works but their UI is built for Western universities, not Nigerian academic structures like CGPA-based attendance."
- "Notion has everything but the setup friction kills non-technical users — your edge is zero-config."

Always tie the weakness to the user's specific market, users, or problem.
If you cannot name a REAL competitor with a SPECIFIC weakness, say so and ask the user who they've seen doing something similar.

═══════════════════════════════════════════════════
OPTIONS RULES
═══════════════════════════════════════════════════
Use [OPTIONS] ONLY for closed-ended questions where choices depend entirely on THIS user's context.
Put [OPTIONS: ...] alone on the very last line. Max 4 options.

SELF-CHECK before every [OPTIONS]:
"Could these same options appear in a conversation about a completely different app?"
→ YES = BANNED. Ask a plain question instead.
→ NO = use them.

CORRECT examples:
[OPTIONS: Face ID liveness check | QR code expires in 60s | GPS locked to lecture hall | NFC student ID tap]
[OPTIONS: Per-university annual license | Per-department monthly fee | TETFUND contract | Students pay per semester]

PERMANENTLY BANNED — never output these:
[OPTIONS: Solo devs | Startup founders | Students | Freelancers | All of these]
[OPTIONS: Yes, African users | No, global | Both]
[OPTIONS: Claude.ai | Cursor | API integration | No AI planned]
[OPTIONS: 2 weeks | 1 month | 3 months | No deadline]
Any option set containing "All of these".

If you can't generate 3+ options specific to THIS app, skip OPTIONS and ask a plain open question.

═══════════════════════════════════════════════════
CONVERSATION FLOW — STRICT SEQUENTIAL
═══════════════════════════════════════════════════
You are having ONE conversation. Track every answer. Never ask about something already answered.

Exchange 1: Name ONE real competitor + their specific weakness. Ask: "What does a user do in the first 60 seconds?"
Exchange 2: Push on their answer from Exchange 1. Ask about the CORE differentiating feature.
[OPTIONS: 3-4 specific features based on what they JUST described]
Exchange 3: Push on their answer from Exchange 2. Ask HOW they deliver that specific thing.
[OPTIONS: specific mechanisms for THAT feature only]
Exchange 4: Ask about monetization — infer from their market.
[OPTIONS: specific pricing models for their exact market]
Exchange 5: Ask about stack — infer from their app type.
[OPTIONS: realistic stacks for their app type]
Exchange 6: Ask about target segment — narrow it down from what they said.
Exchange 7: Ask about AI usage.
Exchange 8+: SHOW_REPORT_BUTTON

CRITICAL RULE — ANSWER TRACKING:
Before asking any question, check: "Did they already answer this?"
If YES → skip it, move to the next exchange.
If they said "clients verify skills" → you know verification method. Ask about HOW clients verify, not whether they verify.
Never introduce a competitor that sells a completely different product to different users.

OPTIONS ATTEMPT RULE:
At exchanges 2-7, always TRY to generate options first.
Only skip if you genuinely cannot make them specific to this app.
When in doubt — generate them. Specific beats generic, generic beats nothing.

═══════════════════════════════════════════════════
MARKET INFERENCE
═══════════════════════════════════════════════════
If the user mentions Nigeria, Africa, or any specific country/market — DO NOT ask about it.
You already know. Inject it into your advice.
Never ask "African users or global?" if they already told you their market.

AFRICA CONTEXT (inject when relevant):
- Mobile-first, Android-heavy, low RAM devices
- Paystack or Flutterwave — never Stripe
- WhatsApp API beats custom push notifications
- USSD fallbacks for non-smartphone users
- Offline-first where possible — data is expensive, campus WiFi is unreliable
- Nigerian university structures: departmental attendance, CGPA impact, NUC requirements
- Compress everything — bandwidth costs are real

═══════════════════════════════════════════════════
TECHNICAL DEPTH GUARDRAIL
═══════════════════════════════════════════════════
If the same technical sub-topic comes up more than twice in a row, stop going deeper.
Say: "That's a deep build decision — we'll break it down after your report. For now:" then ask the next product question.
Your job is product clarity, not technical research.

═══════════════════════════════════════════════════
ABSOLUTE NEVER LIST
═══════════════════════════════════════════════════
- Output JSON in chat responses
- Treat a hackathon, event, university, platform, or client as a competitor
- Ask about funding or team size unless the user brings it up
- Give generic advice like "validate your idea"
- Write more than 3 sentences per response
- Ask two questions at once
- Say "SHOW_REPORT_BUTTON" more than once
- Ask what you can already infer from context
- Ask "African users or global?" when they already told you
- Use ANY option set that could work for a different app

═══════════════════════════════════════════════════
REPORT TRIGGER
═══════════════════════════════════════════════════
If user asks for report/PDF/button in chat → say exactly: "Hit the Generate My Feature Report button below."
NEVER output JSON in chat under any circumstances.

GENERATE_REPORT_NOW — OUTPUT ONLY THIS JSON. NO TEXT BEFORE. NO TEXT AFTER. NO MARKDOWN:
{
  "persona": "solo hacker / startup founder / student / freelancer",
  "stack": "their actual stack or Not specified",
  "claude_usage": "claude.ai / Cursor / API / not using AI",
  "africa_market": true,
  "target_user": "specific user description from conversation",
  "core_problem": "one sentence on the exact problem being solved",
  "readiness_score": 72,
  "readiness_gaps": ["gap 1", "gap 2", "gap 3"],
  "features": [
    {
      "name": "specific feature name",
      "why": "one sentence tied to exact user pain point",
      "efficiency": 85,
      "difficulty": "Easy / Medium / Hard",
      "competitor_gap": "specific thing named real competitors miss that this feature exploits",
      "shipped": false,
      "deadline": null,
      "suggested_additions": ["sub-feature 1", "sub-feature 2"],
      "risk": "biggest implementation risk in one sentence",
      "cut_or_keep": "KEEP",
      "cut_reason": "reason to keep or cut this feature for week 1"
    }
  ],
  "features_to_cut": ["feature name and why it should be cut from v1"],
  "missing_features": [
    {
      "name": "important feature they never mentioned",
      "why": "why this matters for their specific users",
      "priority": "High / Medium"
    }
  ],
  "competitor_radar": "3-4 sentences naming real competitors, their SPECIFIC weaknesses, and exactly how this product exploits those gaps. Be brutal and specific — not generic.",
  "competitor_gaps": [
    {
      "competitor": "Competitor name",
      "weakness": "specific thing they do badly",
      "your_exploit": "exactly how to beat them on this"
    }
  ],
  "build_prompt": "A prompt so specific Claude needs zero follow-up. Include: exact app name, stack, target user demographics, core problem, top 3 features with acceptance criteria, named competitors to beat and HOW specifically, African market requirements if relevant, exact week 1 MVP scope, week 4 success metric, and any constraints the user mentioned.",
  "what_to_build_first": "feature name + one sentence on why this unlocks retention",
  "roadmap": {
    "week1": "exact MVP — what gets built, what hypothesis it tests",
    "week2": "exact addition — what user feedback this unlocks",
    "week3": "exact feature — measurable impact on retention",
    "week4": "polish + launch + how to get first 10 real users"
  },
  "disclaimer": "AI-generated advice. Validate with real users before building."
}
"""

# ── RESEARCHER MODE SYSTEM PROMPT ────────────────────────────────────────────

RESEARCHER_SYSTEM_PROMPT = """
You are DevScope in Researcher Mode — a senior engineer with 10+ years shipping production apps across multiple domains.
A developer or technical researcher is asking you to analyze whether a specific feature makes sense for a specific type of app.
They are NOT pitching their own startup — they want objective, technical, evidence-based analysis.

YOUR ROLE:
- Be a neutral technical advisor, not a challenger
- Give a structured, evidence-backed opinion
- Reference real apps that have or haven't implemented this feature and what happened
- Be specific about tradeoffs, not vague

═══════════════════════════════════════════════════
CONVERSATION FLOW FOR RESEARCHER MODE
═══════════════════════════════════════════════════
You need to gather enough context to give a precise analysis. Ask ONE question at a time.

If the feature and app type are clear from the first message → skip to analysis immediately.
If context is missing → ask for ONLY what you need:
1. What type of app? (if not specified)
2. Who are the end users? (if not specified)
3. What stack are they evaluating for? (if relevant to implementation)

Once you have enough → say: "I have enough context. GENERATE_FEATURE_REPORT_NOW"

PERSONALITY IN RESEARCHER MODE:
- Measured, technical, evidence-based
- Reference real implementations: "Spotify tried X and found Y"
- Name specific libraries, APIs, patterns
- Give a clear VERDICT: BUILD IT / SKIP IT / BUILD LATER
- Max 4 sentences per response before report
- ONE clarifying question at a time
- Never say "Great question" or any filler

AUTO-TRIGGER REPORT:
After 3-5 exchanges (or sooner if context is clear), say exactly:
"Hit the Generate Feature Research Report button below."
Or output: GENERATE_FEATURE_REPORT_NOW

GENERATE_FEATURE_REPORT_NOW — OUTPUT ONLY THIS JSON. NO TEXT BEFORE. NO TEXT AFTER. NO MARKDOWN:
{
  "feature_name": "exact feature name",
  "app_type": "type of app being researched",
  "target_users": "who uses this app",
  "stack": "stack mentioned or Not specified",
  "verdict": "BUILD IT / SKIP IT / BUILD LATER",
  "verdict_reason": "one brutal honest sentence on why",
  "confidence_score": 78,
  "should_build": {
    "reasons": ["reason 1 tied to specific user need", "reason 2", "reason 3"],
    "apps_that_did_it_right": [
      {
        "app": "App name",
        "how_they_did_it": "specific implementation detail",
        "result": "what happened — retention, revenue, drop-off"
      }
    ]
  },
  "should_not_build": {
    "reasons": ["reason 1", "reason 2", "reason 3"],
    "apps_that_got_burned": [
      {
        "app": "App name",
        "what_went_wrong": "specific failure detail",
        "lesson": "what to take from it"
      }
    ]
  },
  "complexity": {
    "effort": "Low / Medium / High",
    "time_estimate": "realistic solo dev estimate",
    "frontend": "what needs to be built on frontend",
    "backend": "what needs to be built on backend",
    "database": "schema changes needed",
    "third_party": "APIs or services needed"
  },
  "alternatives": [
    {
      "name": "alternative feature or approach",
      "why_better": "specific reason this might serve the same need better",
      "tradeoff": "what you lose vs the original feature"
    }
  ],
  "best_libraries": [
    {
      "name": "library or tool name",
      "why": "why this is the best fit",
      "stack_fit": "which stacks this works best with"
    }
  ],
  "build_conditions": "exact conditions under which you SHOULD build this — user threshold, market, data volume, etc.",
  "skip_conditions": "exact conditions under which you should NOT build this — team size, timeline, user stage",
  "week1_implementation": "if they decide to build it, what does week 1 look like exactly",
  "starter_prompt": "A ready-to-paste Claude/Cursor prompt for implementing this feature. Include: feature name, stack, acceptance criteria, edge cases, what done looks like.",
  "disclaimer": "AI-generated analysis. Validate with real users and engineers before committing."
}
"""

FEATURE_ANALYSIS_PROMPT = """
You are a senior developer doing a deep technical analysis of a specific app feature.
Be direct, specific, and actionable. No fluff.

Analyze this feature for the given app context and return ONLY this JSON, no markdown, no explanation:
{
  "feature_name": "name",
  "complexity_breakdown": {
    "frontend": "what needs to be built on frontend",
    "backend": "what needs to be built on backend",
    "database": "what schema/tables are needed",
    "third_party": "any APIs or services needed"
  },
  "existing_solutions": [
    {"name": "tool/library name", "what_it_gives_you_free": "what you don't need to build"}
  ],
  "build_steps": [
    "step 1 — specific and actionable",
    "step 2",
    "step 3",
    "step 4",
    "step 5"
  ],
  "time_estimate": "realistic estimate for a solo dev",
  "biggest_mistake": "the most common mistake devs make building this feature",
  "prompt_for_claude": "Ready-to-paste Claude/Cursor prompt specifically for building this feature. Include stack, acceptance criteria, edge cases to handle, and what done looks like."
}
"""

DEEP_DIVE_PROMPT = """
You are DevScope in Deep Dive mode — a senior engineer who has shipped production apps.
The user just generated their product report. You know their features, target users, competitor gaps, and roadmap.
Now you're going feature by feature, giving brutally specific build guidance tuned to their exact stack.

YOUR JOB PER FEATURE:
- Best tool/library for their EXACT stack (not generic options)
- 3-5 concrete implementation steps
- Biggest gotcha to avoid
- A paste-ready Claude/Cursor starter prompt for that feature

RESPONSE FORMAT — always this structure for each feature:
**[Feature Name]**
**Best approach for [their stack]:** [Name it immediately — no hedging]
**Why:** [1 sentence tied to their stack constraints or market]
**Build steps:**
1. [specific enough to act on today]
2. ...
3. ...
**Watch out for:** [biggest gotcha in one sentence]
**Starter prompt:**
```
[paste-ready prompt, zero placeholders, references their actual stack]
```

---

After covering each feature, ask: "Ready for the next one, or want to go deeper on this?"
[OPTIONS: Go deeper on this | Next feature | Skip to roadmap tips]

RULES:
- Pick ONE best approach per feature — never hedge with "it depends"
- Steps must name actual libraries/APIs relevant to their stack, not abstract steps
- Africa market constraints (offline-first, low RAM, Paystack, WhatsApp API over push notifications) when africa_market is true
- Max 280 words per feature breakdown
- Never re-explain things already covered in a previous feature
- The starter prompt must be copy-pasteable with zero editing needed
"""

PROMPT_GENERATOR_PROMPT = """
You are an expert at writing AI prompts that save developers time and tokens.
Given an app idea and conversation context, generate the most effective prompt possible.

Rules for the prompt you generate:
- So specific that Claude/Cursor needs ZERO follow-up questions
- Includes exact stack, exact users, exact features with acceptance criteria
- Includes what NOT to build (to save tokens)
- Includes the single success metric for week 1
- Written in a way that gets the best code output, not just a plan

Return ONLY this JSON, no markdown:
{
  "quick_prompt": "A short 2-3 sentence prompt for simple tasks",
  "full_prompt": "The complete detailed prompt for starting the full build",
  "cursor_prompt": "Optimized specifically for Cursor AI with file structure hints",
  "token_estimate": "estimated tokens this prompt will use in Claude",
  "what_this_skips": ["thing 1 Claude won't need to ask about", "thing 2", "thing 3"]
}
"""

# ── DB HELPERS ──────────────────────────────────────────────────────────────

def get_or_create_session():
    if "session_id" not in session:
        session_id = str(uuid.uuid4())
        session["session_id"] = session_id
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO sessions (id, title, user_id) VALUES (%s, %s, %s)",
            (session_id, "New Chat", session.get('user_id'))
        )
        conn.commit()
        cursor.close()
        conn.close()
    return session["session_id"]

def get_messages(session_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT role, content FROM messages WHERE session_id = %s ORDER BY timestamp ASC",
        (session_id,)
    )
    messages = cursor.fetchall()
    cursor.close()
    conn.close()
    return [{"role": r, "content": c} for r, c in messages]

def save_message(session_id, role, content):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO messages (session_id, role, content) VALUES (%s, %s, %s)",
        (session_id, role, content)
    )
    conn.commit()
    cursor.close()
    conn.close()

def update_session_title(session_id, title):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "UPDATE sessions SET title = %s WHERE id = %s",
        (title[:50], session_id)
    )
    conn.commit()
    cursor.close()
    conn.close()

def get_report_for_session(session_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, report_json FROM reports WHERE session_id = %s ORDER BY created_at DESC LIMIT 1",
        (session_id,)
    )
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    if row:
        return row[0], json.loads(row[1])
    return None, None

# ── GROQ HELPER ─────────────────────────────────────────────────────────────

def call_groq(messages, max_tokens=1024):
    try:
        response = groq_client.chat.completions.create(
            model=MODEL,
            messages=messages,
            max_tokens=max_tokens,
            temperature=0.3,
            timeout=60
        )
        return response.choices[0].message.content
    except Exception as e:
        print(f"Groq call failed: {e}")
        raise e

def parse_json_response(raw):
    raw = raw.strip()
    raw = re.sub(r'^```json\s*', '', raw)
    raw = re.sub(r'^```\s*', '', raw)
    raw = re.sub(r'```$', '', raw)
    raw = raw.strip()
    match = re.search(r'\{[\s\S]*\}', raw)
    if match:
        raw = match.group(0)
    return json.loads(raw)

# ── COMPETITOR / FEATURE SEARCH ───────────────────────────────────────────────

def search_competitors(app_idea):
    try:
        results = tavily.search(
            query=f"competitors alternatives to {app_idea} startup app weaknesses reviews complaints",
            max_results=5,
            search_depth="basic"
        )
        snippets = [r.get("content", "") for r in results.get("results", [])]
        return "\n".join(snippets[:3])
    except Exception as e:
        print(f"Tavily error: {e}")
        return ""

def search_feature_context(feature_name, app_context):
    try:
        results = tavily.search(
            query=f"how to build {feature_name} feature {app_context} best practices",
            max_results=3,
            search_depth="basic"
        )
        snippets = [r.get("content", "") for r in results.get("results", [])]
        return "\n".join(snippets[:2])
    except Exception as e:
        print(f"Tavily feature search error: {e}")
        return ""

def search_feature_research(feature_name, app_type):
    """Search for real-world examples of feature implementations for researcher mode."""
    try:
        results = tavily.search(
            query=f"{feature_name} feature {app_type} apps implementation examples user feedback",
            max_results=5,
            search_depth="basic"
        )
        snippets = [r.get("content", "") for r in results.get("results", [])]
        return "\n".join(snippets[:3])
    except Exception as e:
        print(f"Tavily research search error: {e}")
        return ""

# ── ASYNC FEATURE ANALYSIS ────────────────────────────────────────────────────

feature_analysis_cache = {}

def analyze_feature_async(report_id, feature, app_context, stack):
    try:
        web_context = search_feature_context(feature['name'], app_context)
        messages = [
            {"role": "system", "content": FEATURE_ANALYSIS_PROMPT},
            {"role": "user", "content": f"""
App context: {app_context}
Stack: {stack}
Feature to analyze: {feature['name']}
Why it's needed: {feature['why']}
Web research: {web_context}

Analyze this feature deeply and return the JSON.
            """}
        ]
        raw = call_groq(messages, max_tokens=1500)
        analysis = parse_json_response(raw)

        cache_key = f"{report_id}_{feature['name']}"
        feature_analysis_cache[cache_key] = {
            "status": "done",
            "data": analysis,
            "completed_at": datetime.now().isoformat()
        }

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT report_json FROM reports WHERE id = %s", (report_id,))
        row = cursor.fetchone()
        if row:
            report_data = json.loads(row[0])
            if "feature_analyses" not in report_data:
                report_data["feature_analyses"] = {}
            report_data["feature_analyses"][feature['name']] = analysis
            cursor.execute(
                "UPDATE reports SET report_json = %s WHERE id = %s",
                (json.dumps(report_data), report_id)
            )
            conn.commit()
        cursor.close()
        conn.close()

    except Exception as e:
        cache_key = f"{report_id}_{feature['name']}"
        feature_analysis_cache[cache_key] = {"status": "error", "error": str(e)}
        print(f"Feature analysis error for {feature['name']}: {e}")

# ── IN-APP NOTIFICATIONS ──────────────────────────────────────────────────────

def check_due_deadlines():
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT id, report_json FROM reports")
        rows = cursor.fetchall()
        cursor.close()
        conn.close()

        notifications = []
        now = datetime.now()

        for report_id, report_json in rows:
            try:
                report_data = json.loads(report_json)
                for f in report_data.get("features", []):
                    if f.get("deadline") and not f.get("shipped"):
                        deadline = datetime.fromisoformat(f["deadline"])
                        diff = now - deadline
                        if diff.total_seconds() > 0:
                            hours_overdue = int(diff.total_seconds() / 3600)
                            notifications.append({
                                "report_id": report_id,
                                "feature": f["name"],
                                "deadline": f["deadline"],
                                "hours_overdue": hours_overdue,
                                "message": f"'{f['name']}' deadline passed {hours_overdue}h ago — have you shipped it?"
                            })
            except Exception:
                continue

        return notifications
    except Exception as e:
        print(f"Deadline check error: {e}")
        return []

# ── PDF GENERATOR (FOUNDER REPORT) ───────────────────────────────────────────

def generate_pdf(report_data):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        rightMargin=0.75*inch, leftMargin=0.75*inch,
        topMargin=0.75*inch, bottomMargin=0.75*inch
    )

    accent = colors.HexColor("#4f9eff")
    dark = colors.HexColor("#0a0a0a")
    grey = colors.HexColor("#555555")
    light_grey = colors.HexColor("#f4f4f4")
    danger = colors.HexColor("#ef4444")
    success = colors.HexColor("#10b981")
    warning_color = colors.HexColor("#f59e0b")

    title_style = ParagraphStyle('Title', fontSize=24, textColor=accent,
                                  fontName='Helvetica-Bold', spaceAfter=4, alignment=TA_CENTER)
    sub_style = ParagraphStyle('Sub', fontSize=10, textColor=grey,
                                fontName='Helvetica', spaceAfter=20, alignment=TA_CENTER)
    section_style = ParagraphStyle('Section', fontSize=9, textColor=grey,
                                    fontName='Helvetica-Bold', spaceBefore=18, spaceAfter=8)
    body_style = ParagraphStyle('Body', fontSize=11, textColor=dark,
                                 fontName='Helvetica', spaceAfter=8, leading=16)
    feature_name_style = ParagraphStyle('FName', fontSize=13, textColor=dark,
                                         fontName='Helvetica-Bold', spaceAfter=4)
    mono_style = ParagraphStyle('Mono', fontSize=9, textColor=grey,
                                 fontName='Courier', spaceAfter=6, leading=14)
    warning_style = ParagraphStyle('Warning', fontSize=10, textColor=danger,
                                    fontName='Helvetica-Bold', spaceAfter=6)
    success_style = ParagraphStyle('Success', fontSize=10, textColor=success,
                                    fontName='Helvetica-Bold', spaceAfter=6)

    story = []

    story.append(Paragraph("⚡ DevScope Report", title_style))
    story.append(Paragraph(f"Generated {datetime.now().strftime('%B %d, %Y at %H:%M')}", sub_style))
    story.append(HRFlowable(width="100%", thickness=2, color=accent, spaceAfter=16))

    readiness = report_data.get("readiness_score", 0)
    story.append(Paragraph("BUILD READINESS SCORE", section_style))
    story.append(Paragraph(f"{readiness}% Ready to Build", body_style))
    if report_data.get("readiness_gaps"):
        story.append(Paragraph("Gaps to close before starting:", mono_style))
        for gap in report_data["readiness_gaps"]:
            story.append(Paragraph(f"• {gap}", mono_style))

    if report_data.get("target_user"):
        story.append(Paragraph("TARGET USER", section_style))
        story.append(Paragraph(report_data["target_user"], body_style))

    if report_data.get("core_problem"):
        story.append(Paragraph("CORE PROBLEM", section_style))
        story.append(Paragraph(report_data["core_problem"], body_style))

    if report_data.get("persona"):
        story.append(Paragraph("DEV PERSONA", section_style))
        story.append(Paragraph(report_data["persona"].title(), body_style))

    if report_data.get("stack"):
        story.append(Paragraph("STACK", section_style))
        story.append(Paragraph(report_data["stack"], body_style))

    if report_data.get("what_to_build_first"):
        story.append(Paragraph("BUILD THIS FIRST", section_style))
        story.append(Paragraph(report_data["what_to_build_first"], body_style))

    if report_data.get("competitor_gaps"):
        story.append(Paragraph("COMPETITOR WEAKNESSES — YOUR OPENINGS", section_style))
        for gap in report_data["competitor_gaps"]:
            story.append(Paragraph(f"❌ {gap['competitor']}: {gap['weakness']}", warning_style))
            story.append(Paragraph(f"→ Your move: {gap['your_exploit']}", body_style))
            story.append(HRFlowable(width="100%", thickness=0.5,
                color=colors.HexColor("#e0e0e0"), spaceAfter=8))

    if report_data.get("features"):
        story.append(Paragraph("FEATURE RECOMMENDATIONS", section_style))
        for f in report_data["features"]:
            cut = f.get("cut_or_keep", "KEEP") == "CUT"
            name_style = warning_style if cut else feature_name_style
            story.append(Paragraph(f"{'❌ CUT: ' if cut else '✅ '}{f['name']}", name_style))
            story.append(Paragraph(f"Why: {f['why']}", body_style))
            story.append(Paragraph(
                f"Difficulty: {f['difficulty']}  |  Efficiency: {f['efficiency']}%", mono_style))
            story.append(Paragraph(f"Competitor Gap: {f['competitor_gap']}", mono_style))
            if f.get("risk"):
                story.append(Paragraph(f"⚠ Risk: {f['risk']}", mono_style))
            if f.get("cut_reason"):
                story.append(Paragraph(f"Note: {f['cut_reason']}", mono_style))
            if f.get("deadline"):
                story.append(Paragraph(f"Deadline: {f['deadline']}", mono_style))
            if f.get("suggested_additions"):
                story.append(Paragraph(
                    f"Consider adding: {', '.join(f['suggested_additions'])}", mono_style))
            story.append(HRFlowable(width="100%", thickness=0.5,
                color=colors.HexColor("#e0e0e0"), spaceAfter=10))

    if report_data.get("missing_features"):
        story.append(Paragraph("FEATURES YOU DIDN'T MENTION (BUT SHOULD)", section_style))
        for f in report_data["missing_features"]:
            story.append(Paragraph(f"+ {f['name']} [{f.get('priority','Medium')}]", warning_style))
            story.append(Paragraph(f["why"], body_style))

    if report_data.get("features_to_cut"):
        story.append(Paragraph("CUT FROM V1", section_style))
        for item in report_data["features_to_cut"]:
            story.append(Paragraph(f"• {item}", mono_style))

    if report_data.get("competitor_radar"):
        story.append(Paragraph("COMPETITOR RADAR", section_style))
        story.append(Paragraph(report_data["competitor_radar"], body_style))

    if report_data.get("roadmap"):
        story.append(Paragraph("4-WEEK ROADMAP", section_style))
        roadmap = report_data["roadmap"]
        table_data = [
            [Paragraph("WEEK", mono_style), Paragraph("WHAT TO SHIP", mono_style)],
            *[[Paragraph(k.upper(), mono_style), Paragraph(v, body_style)]
              for k, v in roadmap.items()]
        ]
        t = Table(table_data, colWidths=[1.2*inch, 5.5*inch])
        t.setStyle(TableStyle([
            ('BACKGROUND', (0, 0), (-1, 0), light_grey),
            ('ROWBACKGROUNDS', (0, 1), (-1, -1), [colors.white, colors.HexColor("#f9f9f9")]),
            ('GRID', (0, 0), (-1, -1), 0.5, colors.HexColor("#e0e0e0")),
            ('VALIGN', (0, 0), (-1, -1), 'TOP'),
            ('TOPPADDING', (0, 0), (-1, -1), 8),
            ('BOTTOMPADDING', (0, 0), (-1, -1), 8),
        ]))
        story.append(t)

    if report_data.get("feature_analyses"):
        story.append(Paragraph("FEATURE DEEP ANALYSIS", section_style))
        for fname, analysis in report_data["feature_analyses"].items():
            story.append(Paragraph(fname, feature_name_style))
            story.append(Paragraph(f"Time estimate: {analysis.get('time_estimate','—')}", mono_style))
            if analysis.get("build_steps"):
                for i, step in enumerate(analysis["build_steps"], 1):
                    story.append(Paragraph(f"{i}. {step}", mono_style))
            story.append(HRFlowable(width="100%", thickness=0.5,
                color=colors.HexColor("#e0e0e0"), spaceAfter=8))

    if report_data.get("build_prompt"):
        story.append(Paragraph("CLAUDE BUILD PROMPT", section_style))
        story.append(Paragraph("Paste this directly into Claude or Cursor:", body_style))
        story.append(Paragraph(report_data["build_prompt"], mono_style))

    story.append(Spacer(1, 20))
    story.append(HRFlowable(width="100%", thickness=0.5,
        color=colors.HexColor("#e0e0e0"), spaceAfter=10))
    disclaimer_style = ParagraphStyle('Disclaimer', fontSize=9, textColor=grey,
        fontName='Helvetica-Oblique', alignment=TA_CENTER)
    story.append(Paragraph(
        report_data.get("disclaimer", "AI-generated advice. Validate with real users."),
        disclaimer_style))

    doc.build(story)
    buffer.seek(0)
    return buffer

# ── PDF GENERATOR (FEATURE RESEARCH REPORT) ──────────────────────────────────

def generate_feature_research_pdf(report_data):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=A4,
        rightMargin=0.75*inch, leftMargin=0.75*inch,
        topMargin=0.75*inch, bottomMargin=0.75*inch
    )

    accent = colors.HexColor("#7c3aed")
    accent2 = colors.HexColor("#4f9eff")
    dark = colors.HexColor("#0a0a0a")
    grey = colors.HexColor("#555555")
    light_grey = colors.HexColor("#f4f4f4")
    danger = colors.HexColor("#ef4444")
    success = colors.HexColor("#10b981")
    warning_color = colors.HexColor("#f59e0b")

    title_style = ParagraphStyle('Title', fontSize=22, textColor=accent,
                                  fontName='Helvetica-Bold', spaceAfter=4, alignment=TA_CENTER)
    sub_style = ParagraphStyle('Sub', fontSize=10, textColor=grey,
                                fontName='Helvetica', spaceAfter=20, alignment=TA_CENTER)
    section_style = ParagraphStyle('Section', fontSize=9, textColor=grey,
                                    fontName='Helvetica-Bold', spaceBefore=18, spaceAfter=8)
    body_style = ParagraphStyle('Body', fontSize=11, textColor=dark,
                                 fontName='Helvetica', spaceAfter=8, leading=16)
    feature_name_style = ParagraphStyle('FName', fontSize=14, textColor=dark,
                                         fontName='Helvetica-Bold', spaceAfter=4)
    mono_style = ParagraphStyle('Mono', fontSize=9, textColor=grey,
                                 fontName='Courier', spaceAfter=6, leading=14)
    verdict_build = ParagraphStyle('VerdictBuild', fontSize=18, textColor=success,
                                    fontName='Helvetica-Bold', spaceAfter=6, alignment=TA_CENTER)
    verdict_skip = ParagraphStyle('VerdictSkip', fontSize=18, textColor=danger,
                                   fontName='Helvetica-Bold', spaceAfter=6, alignment=TA_CENTER)
    verdict_later = ParagraphStyle('VerdictLater', fontSize=18, textColor=warning_color,
                                    fontName='Helvetica-Bold', spaceAfter=6, alignment=TA_CENTER)
    warning_style = ParagraphStyle('Warning', fontSize=10, textColor=danger,
                                    fontName='Helvetica-Bold', spaceAfter=6)
    success_style = ParagraphStyle('Success', fontSize=10, textColor=success,
                                    fontName='Helvetica-Bold', spaceAfter=6)

    story = []

    story.append(Paragraph("🔬 DevScope Feature Research Report", title_style))
    story.append(Paragraph(f"Generated {datetime.now().strftime('%B %d, %Y at %H:%M')}", sub_style))
    story.append(HRFlowable(width="100%", thickness=2, color=accent, spaceAfter=16))

    # Header info
    if report_data.get("feature_name"):
        story.append(Paragraph("FEATURE UNDER ANALYSIS", section_style))
        story.append(Paragraph(report_data["feature_name"], feature_name_style))

    if report_data.get("app_type"):
        story.append(Paragraph(f"App Type: {report_data['app_type']}", mono_style))
    if report_data.get("target_users"):
        story.append(Paragraph(f"Target Users: {report_data['target_users']}", mono_style))
    if report_data.get("stack") and report_data["stack"] != "Not specified":
        story.append(Paragraph(f"Stack: {report_data['stack']}", mono_style))

    # Verdict — big and bold
    verdict = report_data.get("verdict", "")
    verdict_style_map = {
        "BUILD IT": verdict_build,
        "SKIP IT": verdict_skip,
        "BUILD LATER": verdict_later
    }
    v_style = verdict_style_map.get(verdict, verdict_build)
    story.append(Spacer(1, 12))
    story.append(Paragraph(f"VERDICT: {verdict}", v_style))
    confidence = report_data.get("confidence_score", 0)
    story.append(Paragraph(f"Confidence: {confidence}%", sub_style))

    if report_data.get("verdict_reason"):
        story.append(Paragraph(report_data["verdict_reason"], body_style))

    story.append(HRFlowable(width="100%", thickness=1, color=accent, spaceAfter=12))

    # Why you SHOULD build it
    should = report_data.get("should_build", {})
    if should.get("reasons"):
        story.append(Paragraph("✅ REASONS TO BUILD IT", section_style))
        for r in should["reasons"]:
            story.append(Paragraph(f"• {r}", body_style))

    if should.get("apps_that_did_it_right"):
        story.append(Paragraph("Apps that did it right:", mono_style))
        for app_ex in should["apps_that_did_it_right"]:
            story.append(Paragraph(
                f"→ {app_ex['app']}: {app_ex['how_they_did_it']} — {app_ex['result']}", mono_style))

    # Why you SHOULD NOT build it
    should_not = report_data.get("should_not_build", {})
    if should_not.get("reasons"):
        story.append(Paragraph("❌ REASONS TO SKIP IT", section_style))
        for r in should_not["reasons"]:
            story.append(Paragraph(f"• {r}", body_style))

    if should_not.get("apps_that_got_burned"):
        story.append(Paragraph("Apps that got burned:", mono_style))
        for app_ex in should_not["apps_that_got_burned"]:
            story.append(Paragraph(
                f"→ {app_ex['app']}: {app_ex['what_went_wrong']} — Lesson: {app_ex['lesson']}",
                mono_style))

    # Build conditions
    if report_data.get("build_conditions"):
        story.append(Paragraph("BUILD IF:", section_style))
        story.append(Paragraph(report_data["build_conditions"], body_style))

    if report_data.get("skip_conditions"):
        story.append(Paragraph("SKIP IF:", section_style))
        story.append(Paragraph(report_data["skip_conditions"], body_style))

    # Complexity
    complexity = report_data.get("complexity", {})
    if complexity:
        story.append(Paragraph("IMPLEMENTATION COMPLEXITY", section_style))
        story.append(Paragraph(
            f"Effort: {complexity.get('effort','—')}  |  Time: {complexity.get('time_estimate','—')}",
            mono_style))
        if complexity.get("frontend"):
            story.append(Paragraph(f"Frontend: {complexity['frontend']}", mono_style))
        if complexity.get("backend"):
            story.append(Paragraph(f"Backend: {complexity['backend']}", mono_style))
        if complexity.get("database"):
            story.append(Paragraph(f"Database: {complexity['database']}", mono_style))
        if complexity.get("third_party"):
            story.append(Paragraph(f"Third-party: {complexity['third_party']}", mono_style))

    # Alternatives
    alternatives = report_data.get("alternatives", [])
    if alternatives:
        story.append(Paragraph("ALTERNATIVES TO CONSIDER", section_style))
        for alt in alternatives:
            story.append(Paragraph(f"→ {alt['name']}", feature_name_style))
            story.append(Paragraph(f"Why better: {alt['why_better']}", body_style))
            story.append(Paragraph(f"Tradeoff: {alt['tradeoff']}", mono_style))

    # Best libraries
    libs = report_data.get("best_libraries", [])
    if libs:
        story.append(Paragraph("BEST LIBRARIES & TOOLS", section_style))
        for lib in libs:
            story.append(Paragraph(f"• {lib['name']} — {lib['why']} (Best for: {lib['stack_fit']})", mono_style))

    # Week 1 implementation
    if report_data.get("week1_implementation"):
        story.append(Paragraph("WEEK 1 IMPLEMENTATION (IF YOU GO AHEAD)", section_style))
        story.append(Paragraph(report_data["week1_implementation"], body_style))

    # Starter prompt
    if report_data.get("starter_prompt"):
        story.append(Paragraph("READY-TO-PASTE CLAUDE/CURSOR PROMPT", section_style))
        story.append(Paragraph(report_data["starter_prompt"], mono_style))

    story.append(Spacer(1, 20))
    story.append(HRFlowable(width="100%", thickness=0.5,
        color=colors.HexColor("#e0e0e0"), spaceAfter=10))
    disclaimer_style = ParagraphStyle('Disclaimer', fontSize=9, textColor=grey,
        fontName='Helvetica-Oblique', alignment=TA_CENTER)
    story.append(Paragraph(
        report_data.get("disclaimer", "AI-generated analysis. Validate before committing."),
        disclaimer_style))

    doc.build(story)
    buffer.seek(0)
    return buffer

# ── ROUTES ────────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    return render_template("landing.html")

@app.route("/app")
def main_app():
    if 'user_id' not in session:
        return redirect('/')

    pendo_visitor = {'id': session['user_id'], 'name': session.get('user_name', '')}
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT email, created_at FROM users WHERE id = %s", (session['user_id'],))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        if row:
            pendo_visitor['email'] = row[0]
            pendo_visitor['createdAt'] = row[1].isoformat() if row[1] else None
    except Exception:
        pass

    return render_template("index.html", pendo_visitor=pendo_visitor)

# ── MODE SWITCH ROUTE ─────────────────────────────────────────────────────────

@app.route("/mode", methods=["POST"])
def switch_mode():
    """
    Manually switch between founder and researcher mode.
    Expects: { mode: "founder" | "researcher" }
    """
    data = request.json
    mode = data.get("mode", MODE_FOUNDER)
    if mode not in [MODE_FOUNDER, MODE_RESEARCHER]:
        return jsonify({"error": "Invalid mode"}), 400

    set_session_mode(mode)
    session_id = get_or_create_session()

    # Clear chat history when switching modes so context doesn't bleed
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("DELETE FROM messages WHERE session_id = %s", (session_id,))
    conn.commit()
    cursor.close()
    conn.close()

    greeting = {
        MODE_FOUNDER: "Founder mode. No small talk — what are you building?",
        MODE_RESEARCHER: "Researcher mode. What feature are you evaluating, and for what type of app?"
    }

    return jsonify({
        "mode": mode,
        "greeting": greeting[mode],
        "session_id": session_id
    })

@app.route("/mode", methods=["GET"])
def get_mode():
    return jsonify({"mode": get_session_mode()})

# ── MAIN CHAT ROUTE ───────────────────────────────────────────────────────────

@app.route("/chat", methods=["POST"])
def chat():
    try:
        data = request.json
        user_message = data.get("message", "")
        # Allow frontend to force a mode, otherwise auto-detect on first message
        forced_mode = data.get("mode")
        session_id = get_or_create_session()
        history = get_messages(session_id)

        # ── MODE DETECTION ──
        current_mode = get_session_mode()
        if forced_mode and forced_mode in [MODE_FOUNDER, MODE_RESEARCHER]:
            current_mode = forced_mode
            set_session_mode(current_mode)
        elif len(history) == 0:
            # First message — auto-detect
            detected = detect_mode(user_message)
            if detected != current_mode:
                current_mode = detected
                set_session_mode(current_mode)

        # ── SYSTEM PROMPT SELECTION ──
        system_prompt = RESEARCHER_SYSTEM_PROMPT if current_mode == MODE_RESEARCHER else SYSTEM_PROMPT

        # ── WEB SEARCH (for early messages) ──
        web_context = ""
        if len(history) < 6 and len(user_message) > 15:
            if current_mode == MODE_RESEARCHER:
                # Extract feature + app type for targeted search
                web_context_raw = search_feature_research(user_message, "")
            else:
                web_context_raw = search_competitors(user_message)

            if web_context_raw:
                label = (
                    "[LIVE FEATURE RESEARCH — use these real-world examples in your analysis]:\n"
                    if current_mode == MODE_RESEARCHER
                    else "[LIVE COMPETITOR RESEARCH — use these specific weaknesses in your response]:\n"
                )
                web_context = f"\n\n{label}{web_context_raw}\n"

        save_message(session_id, "user", user_message)
        history = get_messages(session_id)

        if len(history) == 1:
            update_session_title(session_id, user_message)

        # Inject recent context + mode lock so the model never drifts
        mode_label = (
            "RESEARCHER MODE — you are a neutral technical advisor analyzing a feature. Do NOT act as a startup challenger."
            if current_mode == MODE_RESEARCHER
            else "FOUNDER MODE — you are a brutally honest startup advisor. Do NOT do generic feature analysis."
        )
        context_note = f"\n\n[ACTIVE MODE: {mode_label}]\n"
        if len(history) > 2:
            context_note += "\n[CONVERSATION HISTORY — read before responding, do NOT repeat what's already been asked]:\n"
            context_note += "\n".join(
                [f"{m['role'].upper()}: {m['content'][:300]}" for m in history[-6:]]
            )

        messages = [{"role": "system", "content": system_prompt + context_note}]
        for i, m in enumerate(history):
            role = "assistant" if m["role"] == "assistant" else "user"
            content = m["content"]
            if i == len(history) - 1 and web_context:
                content += web_context
            messages.append({"role": role, "content": content})

        reply = call_groq(messages)

        # ── JSON LEAK GUARD ──
        is_json_leak = (
            reply.strip().startswith('{') or
            '```json' in reply or
            '"features"' in reply or
            '"persona"' in reply or
            '"roadmap"' in reply or
            '"verdict"' in reply or
            '"feature_name"' in reply
        )

        if is_json_leak:
            if current_mode == MODE_RESEARCHER:
                clean_reply = "Hit the **Generate Feature Research Report** button below."
            else:
                clean_reply = "Hit the **Generate My Feature Report** button below."
            # Only show button if we actually have enough context
            ok, _ = has_enough_context(get_messages(session_id), current_mode)
            show_report = ok
        else:
            triggered = (
                "SHOW_REPORT_BUTTON" in reply or
                "GENERATE_FEATURE_REPORT_NOW" in reply
            )
            clean_reply = reply.replace("SHOW_REPORT_BUTTON", "").replace("GENERATE_FEATURE_REPORT_NOW", "").strip()
            clean_reply = re.sub(r'PERSONA:\s*[\w\s]+', '', clean_reply).strip()

            # Gate the button on minimum context
            if triggered:
                ok, _ = has_enough_context(get_messages(session_id), current_mode)
                show_report = ok
            else:
                show_report = False

        save_message(session_id, "assistant", clean_reply)

        return jsonify({
            "reply": clean_reply,
            "show_report": show_report,
            "session_id": session_id,
            "mode": current_mode  # Always return current mode so frontend can update UI
        })
    except Exception as e:
        print(f"CHAT ERROR: {e}")
        return jsonify({"reply": "Something went wrong. Please try again."}), 500

@app.route('/logout', methods=['POST'])
def logout():
    session.clear()
    return jsonify({'ok': True})

# ── FOUNDER REPORT ROUTE ──────────────────────────────────────────────────────

@app.route("/report", methods=["POST"])
def generate_report():
    try:
        session_id = get_or_create_session()
        history = get_messages(session_id)

        ok, reason = has_enough_context(history, MODE_FOUNDER)
        if not ok:
            return jsonify({"error": reason}), 400

        conversation = "\n".join([f"{m['role'].upper()}: {m['content']}" for m in history])

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": f"Here is the full conversation:\n{conversation}\n\nGENERATE_REPORT_NOW"}
        ]

        raw = call_groq(messages, max_tokens=2500)

        try:
            report_data = parse_json_response(raw)
        except Exception as parse_err:
            print(f"JSON PARSE ERROR: {parse_err}\nRAW: {raw}")
            return jsonify({"error": "Failed to parse report. Try again."}), 500

        report_id = str(uuid.uuid4())
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO reports (id, session_id, report_json) VALUES (%s, %s, %s)",
            (report_id, session_id, json.dumps(report_data))
        )
        conn.commit()
        cursor.close()
        conn.close()

        app_context = report_data.get("core_problem", "app")
        stack = report_data.get("stack", "Not specified")
        for feature in report_data.get("features", []):
            thread = threading.Thread(
                target=analyze_feature_async,
                args=(report_id, feature, app_context, stack),
                daemon=True
            )
            thread.start()

        stack_known = stack and stack.lower() not in ["not specified", "unknown", ""]

        return jsonify({
            "report": report_data,
            "report_id": report_id,
            "report_type": "founder",
            "deep_dive_ready": True,
            "stack_known": stack_known,
            "stack": stack if stack_known else None
        })
    except Exception as e:
        print(f"REPORT ERROR: {e}")
        return jsonify({"error": "Something went wrong generating the report."}), 500

# ── FEATURE RESEARCH REPORT ROUTE ────────────────────────────────────────────

@app.route("/feature-report", methods=["POST"])
def generate_feature_report():
    """
    Generates a Feature Research Report for Researcher mode.
    Reads the current conversation and produces a structured feature analysis JSON.
    """
    try:
        session_id = get_or_create_session()
        history = get_messages(session_id)

        ok, reason = has_enough_context(history, MODE_RESEARCHER)
        if not ok:
            return jsonify({"error": reason}), 400

        conversation = "\n".join([f"{m['role'].upper()}: {m['content']}" for m in history])

        # Pull any feature/app context we can from conversation
        web_context = ""
        try:
            # Try to extract feature name from conversation for targeted search
            feature_match = re.search(
                r"(feature|adding|build|implement)[:\s]+([a-z\s]+?)(?:\s+(?:to|in|for)\s+([a-z\s]+))?[\.,\?]",
                conversation.lower()
            )
            if feature_match:
                feature_hint = feature_match.group(2).strip()
                app_hint = feature_match.group(3).strip() if feature_match.group(3) else ""
                web_context = search_feature_research(feature_hint, app_hint)
        except Exception:
            pass

        messages = [
            {"role": "system", "content": RESEARCHER_SYSTEM_PROMPT},
            {"role": "user", "content": (
                f"Here is the full conversation:\n{conversation}\n\n"
                f"Live research context:\n{web_context}\n\n"
                f"GENERATE_FEATURE_REPORT_NOW"
            )}
        ]

        raw = call_groq(messages, max_tokens=3000)

        try:
            report_data = parse_json_response(raw)
        except Exception as parse_err:
            print(f"FEATURE REPORT JSON PARSE ERROR: {parse_err}\nRAW: {raw}")
            return jsonify({"error": "Failed to parse feature report. Try again."}), 500

        report_id = str(uuid.uuid4())
        # Tag it as a feature research report so download route knows which PDF to generate
        report_data["_report_type"] = "feature_research"

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO reports (id, session_id, report_json) VALUES (%s, %s, %s)",
            (report_id, session_id, json.dumps(report_data))
        )
        conn.commit()
        cursor.close()
        conn.close()

        return jsonify({
            "report": report_data,
            "report_id": report_id,
            "report_type": "feature_research"
        })
    except Exception as e:
        print(f"FEATURE REPORT ERROR: {e}")
        return jsonify({"error": "Something went wrong generating the feature report."}), 500

# ── DEEP DIVE ─────────────────────────────────────────────────────────────────

@app.route("/deep-dive/start", methods=["POST"])
def start_deep_dive():
    try:
        data = request.json
        report_id = data.get("report_id")
        stack_override = data.get("stack", "").strip()
        session_id = get_or_create_session()

        if not report_id:
            return jsonify({"error": "report_id required"}), 400

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT report_json FROM reports WHERE id = %s", (report_id,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()

        if not row:
            return jsonify({"error": "Report not found"}), 404

        report_data = json.loads(row[0])
        stack = stack_override or report_data.get("stack", "Not specified")
        stack_known = stack.lower() not in ["not specified", "unknown", ""]

        features = [f for f in report_data.get("features", []) if f.get("cut_or_keep") != "CUT"]
        feature_list = "\n".join([
            f"- {f['name']} ({f['difficulty']}): {f['why']} | Gap: {f['competitor_gap']}"
            for f in features
        ])

        africa_market = report_data.get("africa_market", False)
        core_problem = report_data.get("core_problem", "")
        target_user = report_data.get("target_user", "")

        system_with_context = DEEP_DIVE_PROMPT + f"""

── REPORT CONTEXT (do NOT ask about any of these — already known) ──
Stack: {stack}
Core problem: {core_problem}
Target user: {target_user}
Africa market constraints apply: {africa_market}

Features to deep dive (priority order, CUT features excluded):
{feature_list}

── BEHAVIOUR ──
{"Your FIRST message: ask for their stack before starting. Use [OPTIONS] with 4 realistic stack choices inferred from their app type — not generic." if not stack_known else "Stack is confirmed. Start immediately with the FIRST feature. No preamble."}
After each feature breakdown, always end with:
[OPTIONS: Go deeper on this | Next feature | Skip to roadmap tips]
"""

        trigger = (
            "What stack are they using?" if not stack_known
            else f"Start the deep dive. Stack is {stack}. Go feature by feature."
        )

        messages = [
            {"role": "system", "content": system_with_context},
            {"role": "user", "content": trigger}
        ]

        reply = call_groq(messages, max_tokens=1200)
        save_message(session_id, "assistant", reply)

        return jsonify({
            "reply": reply,
            "session_id": session_id,
            "stack_needed": not stack_known
        })

    except Exception as e:
        print(f"DEEP DIVE START ERROR: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/deep-dive/next", methods=["POST"])
def deep_dive_next():
    try:
        data = request.json
        report_id = data.get("report_id")
        confirmed_stack = data.get("stack", "").strip()

        if not report_id or not confirmed_stack:
            return jsonify({"error": "report_id and stack required"}), 400

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT report_json FROM reports WHERE id = %s", (report_id,))
        row = cursor.fetchone()
        if row:
            report_data = json.loads(row[0])
            report_data["stack"] = confirmed_stack
            cursor.execute(
                "UPDATE reports SET report_json = %s WHERE id = %s",
                (json.dumps(report_data), report_id)
            )
            conn.commit()
        cursor.close()
        conn.close()

        return start_deep_dive_with(report_id, confirmed_stack)

    except Exception as e:
        print(f"DEEP DIVE NEXT ERROR: {e}")
        return jsonify({"error": str(e)}), 500

def start_deep_dive_with(report_id, stack):
    session_id = get_or_create_session()

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT report_json FROM reports WHERE id = %s", (report_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()

    report_data = json.loads(row[0])
    features = [f for f in report_data.get("features", []) if f.get("cut_or_keep") != "CUT"]
    feature_list = "\n".join([
        f"- {f['name']} ({f['difficulty']}): {f['why']} | Gap: {f['competitor_gap']}"
        for f in features
    ])

    system_with_context = DEEP_DIVE_PROMPT + f"""

── REPORT CONTEXT ──
Stack: {stack}
Core problem: {report_data.get('core_problem', '')}
Target user: {report_data.get('target_user', '')}
Africa market constraints apply: {report_data.get('africa_market', False)}

Features to deep dive (priority order):
{feature_list}

Stack is confirmed. Start immediately with the FIRST feature. No preamble.
After each feature always end with:
[OPTIONS: Go deeper on this | Next feature | Skip to roadmap tips]
"""

    messages = [
        {"role": "system", "content": system_with_context},
        {"role": "user", "content": f"Stack confirmed: {stack}. Begin the deep dive."}
    ]

    reply = call_groq(messages, max_tokens=1200)
    save_message(session_id, "assistant", reply)

    return jsonify({
        "reply": reply,
        "session_id": session_id,
        "stack_needed": False
    })

# ── REMAINING ROUTES ──────────────────────────────────────────────────────────

@app.route("/report/<report_id>/feature-analysis", methods=["GET"])
def get_feature_analysis(report_id):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT report_json FROM reports WHERE id = %s", (report_id,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()

        if not row:
            return jsonify({"error": "Report not found"}), 404

        report_data = json.loads(row[0])
        analyses = report_data.get("feature_analyses", {})
        total_features = len(report_data.get("features", []))
        completed = len(analyses)

        return jsonify({
            "analyses": analyses,
            "completed": completed,
            "total": total_features,
            "done": completed >= total_features
        })
    except Exception as e:
        print(f"FEATURE ANALYSIS POLL ERROR: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/report/<report_id>/generate-prompts", methods=["POST"])
def generate_prompts(report_id):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT report_json FROM reports WHERE id = %s", (report_id,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()

        if not row:
            return jsonify({"error": "Report not found"}), 404

        report_data = json.loads(row[0])

        context = f"""
App: {report_data.get('core_problem', 'Not specified')}
Target user: {report_data.get('target_user', 'Not specified')}
Stack: {report_data.get('stack', 'Not specified')}
Features: {', '.join([f['name'] for f in report_data.get('features', [])])}
Build first: {report_data.get('what_to_build_first', 'Not specified')}
Week 1 goal: {report_data.get('roadmap', {}).get('week1', 'Not specified')}
Competitors: {report_data.get('competitor_radar', 'Not specified')}
Competitor gaps: {json.dumps(report_data.get('competitor_gaps', []))}
Africa market: {report_data.get('africa_market', False)}
Claude usage plan: {report_data.get('claude_usage', 'Not specified')}
        """

        messages = [
            {"role": "system", "content": PROMPT_GENERATOR_PROMPT},
            {"role": "user", "content": f"Generate optimized prompts for this project:\n{context}"}
        ]

        raw = call_groq(messages, max_tokens=2000)
        prompt_data = parse_json_response(raw)

        return jsonify({"prompts": prompt_data})
    except Exception as e:
        print(f"PROMPT GENERATION ERROR: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/report/<report_id>/download", methods=["GET"])
def download_report(report_id):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT report_json FROM reports WHERE id = %s", (report_id,))
        row = cursor.fetchone()
        cursor.close()
        conn.close()
        if not row:
            return "Report not found", 404

        report_data = json.loads(row[0])

        # Route to correct PDF generator based on report type
        if report_data.get("_report_type") == "feature_research":
            pdf_buffer = generate_feature_research_pdf(report_data)
            filename = f'devscope-feature-research-{report_id[:8]}.pdf'
        else:
            pdf_buffer = generate_pdf(report_data)
            filename = f'devscope-report-{report_id[:8]}.pdf'

        return send_file(
            pdf_buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=filename
        )
    except Exception as e:
        print(f"PDF ERROR: {e}")
        return "Failed to generate PDF", 500

@app.route("/notifications", methods=["GET"])
def get_notifications():
    try:
        notifications = check_due_deadlines()
        return jsonify({"notifications": notifications})
    except Exception as e:
        return jsonify({"notifications": []})

@app.route("/feature/deadline", methods=["POST"])
def set_feature_deadline():
    try:
        data = request.json
        report_id = data.get("report_id")
        feature_name = data.get("feature_name")
        deadline_str = data.get("deadline")
        datetime.fromisoformat(deadline_str)

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT report_json FROM reports WHERE id = %s", (report_id,))
        row = cursor.fetchone()
        if row:
            report_data = json.loads(row[0])
            for f in report_data.get("features", []):
                if f["name"] == feature_name:
                    f["deadline"] = deadline_str
            cursor.execute(
                "UPDATE reports SET report_json = %s WHERE id = %s",
                (json.dumps(report_data), report_id)
            )
            conn.commit()
        cursor.close()
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        print(f"DEADLINE ERROR: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/feature/toggle", methods=["POST"])
def toggle_feature():
    try:
        data = request.json
        report_id = data.get("report_id")
        feature_name = data.get("feature_name")

        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT report_json FROM reports WHERE id = %s", (report_id,))
        row = cursor.fetchone()
        if not row:
            return jsonify({"error": "Report not found"}), 404

        report_data = json.loads(row[0])
        for f in report_data.get("features", []):
            if f["name"] == feature_name:
                f["shipped"] = not f.get("shipped", False)

        cursor.execute(
            "UPDATE reports SET report_json = %s WHERE id = %s",
            (json.dumps(report_data), report_id)
        )
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({"success": True, "report": report_data})
    except Exception as e:
        print(f"TOGGLE ERROR: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/sessions", methods=["GET"])
def get_sessions():
    user_id = session.get('user_id')
    if not user_id:
        return jsonify([])
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, title, created_at FROM sessions WHERE user_id = %s ORDER BY created_at DESC",
        (user_id,)
    )
    rows = cursor.fetchall()
    cursor.close()
    conn.close()
    return jsonify([{"id": r[0], "title": r[1], "created_at": str(r[2])} for r in rows])

@app.route("/sessions/<session_id>", methods=["GET"])
def get_session(session_id):
    messages = get_messages(session_id)
    return jsonify({"messages": messages})

@app.route("/sessions/<session_id>", methods=["DELETE"])
def delete_session(session_id):
    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute("DELETE FROM messages WHERE session_id = %s", (session_id,))
        cursor.execute("DELETE FROM reports WHERE session_id = %s", (session_id,))
        cursor.execute("DELETE FROM sessions WHERE id = %s", (session_id,))
        conn.commit()
        cursor.close()
        conn.close()
        if session.get("session_id") == session_id:
            session.pop("session_id", None)
        return jsonify({"success": True})
    except Exception as e:
        print(f"DELETE ERROR: {e}")
        return jsonify({"error": str(e)}), 500

@app.route("/new_chat", methods=["POST"])
def new_chat():
    new_id = str(uuid.uuid4())
    session["session_id"] = new_id
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "INSERT INTO sessions (id, title, user_id) VALUES (%s, %s, %s)",
        (new_id, "New Chat", session.get('user_id'))
    )
    conn.commit()
    cursor.close()
    conn.close()
    return jsonify({"session_id": new_id})

@app.route("/share/<report_id>", methods=["GET"])
def share_report(report_id):
    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT report_json FROM reports WHERE id = %s", (report_id,))
    row = cursor.fetchone()
    cursor.close()
    conn.close()
    if not row:
        return "Report not found", 404
    report_data = json.loads(row[0])
    return render_template("index.html", shared_report=report_data, report_id=report_id)

from werkzeug.security import generate_password_hash, check_password_hash

@app.route('/register', methods=['POST'])
def register():
    data = request.json
    name = data.get('name', '').strip()
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')

    if not name or not email or len(password) < 8:
        return jsonify({'ok': False, 'error': 'Invalid input'}), 400

    try:
        conn = get_connection()
        cursor = conn.cursor()
        cursor.execute(
            "INSERT INTO users (name, email, password_hash) VALUES (%s, %s, %s)",
            (name, email, generate_password_hash(password))
        )
        conn.commit()
        cursor.close()
        conn.close()
        return jsonify({'ok': True})
    except Exception as e:
        return jsonify({'ok': False, 'error': 'Email already registered'}), 409

@app.route('/login', methods=['POST'])
def login():
    data = request.json
    email = data.get('email', '').strip().lower()
    password = data.get('password', '')

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute(
        "SELECT id, name, password_hash FROM users WHERE email = %s", (email,)
    )
    user = cursor.fetchone()
    cursor.close()
    conn.close()

    if not user or not check_password_hash(user[2], password):
        return jsonify({'ok': False, 'error': 'Invalid email or password'}), 401

    session['user_id'] = user[0]
    session['user_name'] = user[1]
    return jsonify({'ok': True})

from authlib.integrations.flask_client import OAuth

oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={'scope': 'openid email profile'}
)

@app.route('/auth/google')
def google_login():
    redirect_uri = url_for('google_callback', _external=True)
    return google.authorize_redirect(redirect_uri)

@app.route('/auth/google/callback')
def google_callback():
    token = google.authorize_access_token()
    user_info = token['userinfo']
    email = user_info['email']
    name = user_info.get('name', email.split('@')[0])

    conn = get_connection()
    cursor = conn.cursor()
    cursor.execute("SELECT id, name FROM users WHERE email = %s", (email,))
    user = cursor.fetchone()
    if not user:
        cursor.execute(
            "INSERT INTO users (name, email, password_hash) VALUES (%s, %s, %s)",
            (name, email, generate_password_hash(str(uuid.uuid4())))
        )
        conn.commit()
        cursor.execute("SELECT id, name FROM users WHERE email = %s", (email,))
        user = cursor.fetchone()
    cursor.close()
    conn.close()

    session['user_id'] = user[0]
    session['user_name'] = user[1]
    return redirect('/app')

if __name__ == "__main__":
    init_db()
    app.run(debug=True)