from flask import Flask, request, jsonify, send_from_directory
from flask_cors import CORS
import pymysql
import time

app = Flask(__name__, static_folder='.')
CORS(app)

def get_db():
    return pymysql.connect(
        host='emr-db',
        user='root',
        password='mariadbpassword',
        database='emr_db',
        charset='utf8mb4',
        cursorclass=pymysql.cursors.DictCursor,
        autocommit=True
    )

def init_db():
    print("Starting DB initialization...")
    for i in range(20):
        try:
            conn = get_db()
            with conn.cursor() as cursor:
                # 기존 테이블을 안전하게 삭제 후 재생성 (테스트 단계이므로 초기화)
                cursor.execute("DROP TABLE IF EXISTS chart_records;")
                cursor.execute("DROP TABLE IF EXISTS patients;")

                # 1. 환자 마스터 테이블
                cursor.execute("""
                    CREATE TABLE patients (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        patient_name VARCHAR(100) NOT NULL,
                        species VARCHAR(50) DEFAULT '',
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
                """)
                
                # 2. SOAP 진료 기록 테이블
                cursor.execute("""
                    CREATE TABLE chart_records (
                        id INT AUTO_INCREMENT PRIMARY KEY,
                        patient_id INT NOT NULL,
                        weight VARCHAR(20) DEFAULT '',
                        temp VARCHAR(20) DEFAULT '',
                        subjective TEXT,
                        objective TEXT,
                        assessment TEXT,
                        plan TEXT,
                        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                        FOREIGN KEY (patient_id) REFERENCES patients(id) ON DELETE CASCADE
                    ) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
                """)
            conn.close()
            print("DB Initialized Successfully with Separated Tables.")
            return True
        except Exception as e:
            print(f"Waiting for DB... ({i+1}/20) Err: {e}")
            time.sleep(3)
    return False

@app.route('/')
def index():
    return send_from_directory('.', 'index.html')

# 환자 검색 또는 목록 조회 API
@app.route('/api/patients', methods=['GET'])
def get_patients():
    try:
        search = request.args.get('search', '').strip()
        conn = get_db()
        with conn.cursor() as cursor:
            if search:
                cursor.execute("SELECT * FROM patients WHERE patient_name LIKE %s ORDER BY id DESC", (f"%{search}%",))
            else:
                cursor.execute("SELECT * FROM patients ORDER BY id DESC LIMIT 20")
            res = cursor.fetchall()
        conn.close()
        return jsonify(res if res else [])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# 특정 환자의 진료 기록(SOAP) 목록 조회 API
@app.route('/api/patients/<int:patient_id>/records', methods=['GET'])
def get_patient_records(patient_id):
    try:
        conn = get_db()
        with conn.cursor() as cursor:
            cursor.execute("""
                SELECT r.*, p.patient_name, p.species 
                FROM chart_records r
                JOIN patients p ON r.patient_id = p.id
                WHERE r.patient_id = %s
                ORDER BY r.id DESC
            """, (patient_id,))
            res = cursor.fetchall()
        conn.close()
        return jsonify(res if res else [])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# 전체 최근 진료 기록 조회 API (메인 화면용)
@app.route('/api/records', methods=['GET'])
def get_records():
    try:
        search = request.args.get('search', '').strip()
        conn = get_db()
        with conn.cursor() as cursor:
            sql = """
                SELECT r.*, p.patient_name, p.species 
                FROM chart_records r
                JOIN patients p ON r.patient_id = p.id
            """
            if search:
                sql += " WHERE p.patient_name LIKE %s ORDER BY r.id DESC"
                cursor.execute(sql, (f"%{search}%",))
            else:
                sql += " ORDER BY r.id DESC LIMIT 30"
                cursor.execute(sql)
            res = cursor.fetchall()
        conn.close()
        return jsonify(res if res else [])
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# 신규 진료 기록 저장 API (환자가 없으면 자동 등록 후 SOAP 추가)
@app.route('/api/records', methods=['POST'])
def create_record():
    try:
        data = request.get_json(force=True, silent=True) or {}
        patient_name = data.get('patient_name')
        species = data.get('species', '')
        if not patient_name:
            return jsonify({"error": "환자명 필요"}), 400

        conn = get_db()
        with conn.cursor() as cursor:
            # 1. 환자가 이미 존재하는지 확인
            cursor.execute("SELECT id FROM patients WHERE patient_name = %s AND species = %s", (patient_name, species))
            patient = cursor.fetchone()

            if patient:
                patient_id = patient['id']
            else:
                # 없으면 환자 마스터에 새로 등록
                cursor.execute("INSERT INTO patients (patient_name, species) VALUES (%s, %s)", (patient_name, species))
                patient_id = cursor.lastrowid

            # 2. 해당 환자의 새로운 방문 SOAP 기록 추가
            sql = """
                INSERT INTO chart_records 
                (patient_id, weight, temp, subjective, objective, assessment, plan) 
                VALUES (%s, %s, %s, %s, %s, %s, %s)
            """
            cursor.execute(sql, (
                patient_id,
                str(data.get('weight', '')),
                str(data.get('temp', '')),
                str(data.get('subjective', '')),
                str(data.get('objective', '')),
                str(data.get('assessment', '')),
                str(data.get('plan', ''))
            ))
        conn.close()
        return jsonify({"status": "success"}), 201
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# 진료 기록 수정 API
@app.route('/api/records/<int:id>', methods=['PUT'])
def update_record(id):
    try:
        data = request.get_json(force=True, silent=True) or {}
        conn = get_db()
        with conn.cursor() as cursor:
            sql = """
                UPDATE chart_records SET 
                weight=%s, temp=%s, subjective=%s, objective=%s, assessment=%s, plan=%s 
                WHERE id=%s
            """
            cursor.execute(sql, (
                str(data.get('weight', '')),
                str(data.get('temp', '')),
                str(data.get('subjective', '')),
                str(data.get('objective', '')),
                str(data.get('assessment', '')),
                str(data.get('plan', '')),
                id
            ))
        conn.close()
        return jsonify({"status": "updated"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# 진료 기록 삭제 API
@app.route('/api/records/<int:id>', methods=['DELETE'])
def delete_record(id):
    try:
        conn = get_db()
        with conn.cursor() as cursor:
            cursor.execute("DELETE FROM chart_records WHERE id=%s", (id,))
        conn.close()
        return jsonify({"status": "deleted"})
    except Exception as e:
        return jsonify({"error": str(e)}), 500

if __name__ == '__main__':
    init_db()
    app.run(host='0.0.0.0', port=5000)