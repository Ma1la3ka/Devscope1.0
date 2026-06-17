import mysql.connector
from config import MYSQL_HOST, MYSQL_USER, MYSQL_PASSWORD, MYSQL_DB, MYSQL_PORT

def get_connection():
    return mysql.connector.connect(
        host=MYSQL_HOST,
        port=int(MYSQL_PORT),
        user=MYSQL_USER,
        password=MYSQL_PASSWORD,
        database=MYSQL_DB,
        ssl_disabled=False
    )

def init_db():
    conn = get_connection()
    cursor = conn.cursor()

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS users (
            id INT NOT NULL AUTO_INCREMENT,
            name VARCHAR(100) NOT NULL,
            email VARCHAR(255) NOT NULL,
            password_hash VARCHAR(255) NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            UNIQUE KEY email (email)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS sessions (
            id VARCHAR(36) NOT NULL,
            title TEXT,
            persona TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            user_id INT DEFAULT NULL,
            PRIMARY KEY (id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS messages (
            id INT AUTO_INCREMENT NOT NULL,
            session_id VARCHAR(36) DEFAULT NULL,
            role TEXT,
            content LONGTEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            KEY session_id (session_id)
        )
    """)

    cursor.execute("""
        CREATE TABLE IF NOT EXISTS reports (
            id VARCHAR(36) NOT NULL,
            session_id VARCHAR(36) DEFAULT NULL,
            report_json LONGTEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (id),
            KEY session_id (session_id)
        )
    """)

    conn.commit()
    cursor.close()
    conn.close()
    print("Database initialized successfully")

if __name__ == "__main__":
    init_db()