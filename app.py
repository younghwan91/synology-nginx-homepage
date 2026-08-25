from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
from contextlib import contextmanager
import pymysql
import os
import time

app = Flask(__name__, static_folder='.')
CORS(app)

DB_CONFIG = {
    'host': os.environ.get('DB_HOST', 'emr-db'),
    'user': os.environ.get('DB_USER', 'root'),
    'password': os.environ.get('DB_PASSWORD', 'mariadbpassword'),
    'database': os.environ.get('DB_NAME', 'emr_db'),
    'charset': 'utf8mb4',
    'cursorclass': pymysql.cursors.DictCursor,
    'autocommit': True,
}


def get_db():
    return pymysql.connect(**DB_CONFIG)


@contextmanager
def db_cursor():
    """예외가 나도 커넥션을 반드시 닫는다."""
    conn = get_db()
    try:
        with conn.cursor() as cursor:
            yield cursor
    finally:
        conn.close()


def init_db():
    """멱등 초기화. 기존 데이터는 절대 지우지 않는다."""
    print("Starting DB initialization...")
    for i in range(20):
        try:
            with db_cursor() as cursor:
                # 1. 환자 마스터 테이블
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS patients (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        patient_name VARCHAR(100) NOT NULL,
                        guardian_phone VARCHAR(20) NOT NULL DEFAULT '',
                        species VARCHAR(50) NOT NULL DEFAULT '',
                        alert_memo VARCHAR(255) NOT NULL DEFAULT '',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
                """)

                # 2. SOAP 진료 기록 테이블
                cursor.execute("""
                    CREATE TABLE IF NOT EXISTS chart_records (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        patient_id INT NOT NULL,
                        weight VARCHAR(20) NOT NULL DEFAULT '',
                        temp VARCHAR(20) NOT NULL DEFAULT '',
                        subjective TEXT,
                        objective TEXT,
                        assessment TEXT,
                        plan TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
                """)

                # 구버전 스키마에서 올라온 경우를 위한 마이그레이션
                cursor.execute("""
                    ALTER TABLE patients
                    ADD COLUMN IF NOT EXISTS guardian_phone VARCHAR(20) NOT NULL DEFAULT '' AFTER patient_name;
                """)
                cursor.execute("""
                    ALTER TABLE patients
                    ADD COLUMN IF NOT EXISTS alert_memo VARCHAR(255) NOT NULL DEFAULT '' AFTER species;
                """)
                cursor.execute("""
                    ALTER TABLE patients
                    ADD INDEX IF NOT EXISTS idx_patients_name (patient_name);
                """)
                cursor.execute("""
                    ALTER TABLE patients
                    ADD INDEX IF NOT EXISTS idx_patients_phone (guardian_phone);
                """)
                cursor.execute("""
                    ALTER TABLE chart_records
                    ADD INDEX IF NOT EXISTS idx_records_patient (patient_id, id);
                """)
            print("DB Initialized Successfully (non-destructive).")
            return True
        except Exception as e:
            print(f"Waiting for DB... ({i+1}/20) Err: {e}")
            time.sleep(3)
    return False


def _s(data, key, limit=None):
    """요청 본문에서 문자열 필드를 안전하게 꺼낸다."""
    val = str(data.get(key, '') or '').strip()
    return val[:limit] if limit else val


RECORD_SELECT = """
    SELECT r.*, p.patient_name, p.guardian_phone, p.species, p.alert_memo
    FROM chart_records r
    JOIN patients p ON r.patient_id = p.id
"""


@app.route('/')
def index():
    return send_from_directory('.', 'index.html')


# 환자 검색 또는 목록 조회 API (환자명 / 연락처)
@app.route('/api/patients', methods=['GET'])
def get_patients():
    try:
        search = request.args.get('search', '').strip()
        with db_cursor() as cursor:
            if search:
                cursor.execute(
                    """SELECT * FROM patients
                       WHERE patient_name LIKE %s OR REPLACE(guardian_phone, '-', '') LIKE %s
                       ORDER BY id DESC""",
                    (f"%{search}%", f"%{search.replace('-', '')}%"),
                )
            else:
                cursor.execute("SELECT * FROM patients ORDER BY id DESC LIMIT 20")
            res = cursor.fetchall()
        return jsonify(res or [])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# 특정 환자의 진료 기록(SOAP) 전체 이력 조회 API
@app.route('/api/patients/<int:patient_id>/records', methods=['GET'])
def get_patient_records(patient_id):
    try:
        with db_cursor() as cursor:
            cursor.execute(RECORD_SELECT + " WHERE r.patient_id = %s ORDER BY r.id DESC", (patient_id,))
            res = cursor.fetchall()
        return jsonify(res or [])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# 전체 최근 진료 기록 조회 API (메인 화면용)
@app.route('/api/records', methods=['GET'])
def get_records():
    try:
        search = request.args.get('search', '').strip()
        with db_cursor() as cursor:
            if search:
                cursor.execute(
                    RECORD_SELECT +
                    """ WHERE p.patient_name LIKE %s OR REPLACE(p.guardian_phone, '-', '') LIKE %s
                        ORDER BY r.id DESC LIMIT 200""",
                    (f"%{search}%", f"%{search.replace('-', '')}%"),
                )
            else:
                cursor.execute(RECORD_SELECT + " ORDER BY r.id DESC LIMIT 30")
            res = cursor.fetchall()
        return jsonify(res or [])
    except Exception as e:
        return jsonify({"error": str(e)}), 500


def _upsert_patient(cursor, name, phone, species, alert_memo):
    """연락처가 있으면 (이름, 연락처)로, 없으면 (이름, 품종)으로 환자를 식별한다."""
    if phone:
        cursor.execute(
            "SELECT id FROM patients WHERE patient_name = %s AND guardian_phone = %s",
            (name, phone),
        )
    else:
        cursor.execute(
            "SELECT id FROM patients WHERE patient_name = %s AND guardian_phone = '' AND species = %s",
            (name, species),
        )
    patient = cursor.fetchone()

    if patient:
        # 재진 시 갱신된 품종/경고메모를 마스터에 반영 (빈 값으로 덮어쓰지는 않음)
        cursor.execute(
            """UPDATE patients
               SET species = COALESCE(NULLIF(%s, ''), species),
                   alert_memo = %s
               WHERE id = %s""",
            (species, alert_memo, patient['id']),
        )
        return patient['id']

    cursor.execute(
        "INSERT INTO patients (patient_name, guardian_phone, species, alert_memo) VALUES (%s, %s, %s, %s)",
        (name, phone, species, alert_memo),
    )
    return cursor.lastrowid


# 신규 진료 기록 저장 API (환자가 없으면 자동 등록 후 SOAP 추가)
@app.route('/api/records', methods=['POST'])
def create_record():
    try:
        data = request.get_json(force=True, silent=True) or {}
        patient_name = _s(data, 'patient_name', 100)
        if not patient_name:
            return jsonify({"error": "환자명 필요"}), 400

        with db_cursor() as cursor:
            patient_id = _upsert_patient(
                cursor,
                patient_name,
                _s(data, 'guardian_phone', 20),
                _s(data, 'species', 50),
                _s(data, 'alert_memo', 255),
            )
            cursor.execute(
                """INSERT INTO chart_records
                   (patient_id, weight, temp, subjective, objective, assessment, plan)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (
                    patient_id,
                    _s(data, 'weight', 20),
                    _s(data, 'temp', 20),
                    _s(data, 'subjective'),
                    _s(data, 'objective'),
                    _s(data, 'assessment'),
                    _s(data, 'plan'),
                ),
            )
            record_id = cursor.lastrowid
        return jsonify({"status": "success", "id": record_id, "patient_id": patient_id}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# 진료 기록 수정 API (SOAP + 환자 마스터 동시 갱신)
@app.route('/api/records/<int:id>', methods=['PUT'])
def update_record(id):
    try:
        data = request.get_json(force=True, silent=True) or {}
        with db_cursor() as cursor:
            cursor.execute("SELECT patient_id FROM chart_records WHERE id = %s", (id,))
            row = cursor.fetchone()
            if not row:
                return jsonify({"error": "해당 진료 기록이 없습니다."}), 404

            cursor.execute(
                """UPDATE chart_records SET
                   weight=%s, temp=%s, subjective=%s, objective=%s, assessment=%s, plan=%s
                   WHERE id=%s""",
                (
                    _s(data, 'weight', 20),
                    _s(data, 'temp', 20),
                    _s(data, 'subjective'),
                    _s(data, 'objective'),
                    _s(data, 'assessment'),
                    _s(data, 'plan'),
                    id,
                ),
            )

            patient_name = _s(data, 'patient_name', 100)
            if patient_name:
                cursor.execute(
                    """UPDATE patients SET
                       patient_name=%s, guardian_phone=%s, species=%s, alert_memo=%s
                       WHERE id=%s""",
                    (
                        patient_name,
                        _s(data, 'guardian_phone', 20),
                        _s(data, 'species', 50),
                        _s(data, 'alert_memo', 255),
                        row['patient_id'],
                    ),
                )
        return jsonify({"status": "updated"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


# 진료 기록 삭제 API
@app.route('/api/records/<int:id>', methods=['DELETE'])
def delete_record(id):
    try:
        with db_cursor() as cursor:
            cursor.execute("DELETE FROM chart_records WHERE id=%s", (id,))
            deleted = cursor.rowcount
        if not deleted:
            return jsonify({"error": "해당 진료 기록이 없습니다."}), 404
        return jsonify({"status": "deleted"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500


if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000)
