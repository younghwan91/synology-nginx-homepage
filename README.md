# 동물병원 EMR

동물병원용 SOAP 차트 기록 시스템. 단일 페이지 프론트엔드 + Flask API + MariaDB로 구성된
Docker Compose 스택이며, 시놀로지 NAS에서 구동하는 것을 전제로 합니다.

> 이 저장소는 원래 시놀로지 nginx 홈페이지 백업용이었고, 2026년 8월부터 EMR 시스템으로
> 용도가 바뀌었습니다.

## 구성

| 파일 | 역할 |
|---|---|
| `index.html` | 단일 페이지 UI. 별도 빌드 과정 없음 (프레임워크·번들러 미사용) |
| `app.py` | Flask REST API. 정적 파일 서빙과 스키마 초기화도 겸함 |
| `docker-compose.yml` | `emr-db`(MariaDB 10.6) + `emr-backend`(Python 3.10) 2개 서비스 |
| `requirements.txt` | 파이썬 의존성 |

프론트엔드는 `/`에서 `app.py`가 직접 서빙하므로 별도 웹서버가 필요 없습니다.

## 실행

```sh
cp .env.example .env      # DB_PASSWORD 를 실제 값으로 변경할 것
docker compose up -d
```

`http://<호스트>:8080` 으로 접속합니다. 컨테이너 내부 5000번 포트가 8080으로 매핑됩니다.

DB는 healthcheck가 통과한 뒤에야 백엔드가 기동하며, 스키마는 백엔드 시작 시 자동으로
생성·갱신됩니다.

### 환경변수

`.env` 파일로 주입합니다. 값이 없으면 괄호 안의 기본값이 쓰입니다.

| 변수 | 기본값 | 설명 |
|---|---|---|
| `DB_PASSWORD` | `mariadbpassword` | MariaDB root 비밀번호 |
| `DB_NAME` | `emr_db` | 데이터베이스 이름 |
| `DB_HOST` | `emr-db` | 백엔드가 접속할 DB 호스트 |
| `DB_USER` | `root` | DB 계정 |

기본 비밀번호는 반드시 바꿔서 쓰십시오. `.env`와 `db_data/`는 `.gitignore`에 등록되어 있습니다.

## 데이터 모델

환자 마스터와 진료 기록을 분리한 2테이블 구조입니다. 한 환자가 여러 번 내원하면
`patients` 행 하나에 `chart_records` 행이 여러 개 달립니다.

```
patients                          chart_records
--------                          -------------
id              PK        <---+   id              PK
patient_name    (idx)         +-- patient_id      FK, ON DELETE CASCADE
guardian_phone  (idx)             weight, temp
species                           subjective  \
alert_memo                        objective    |  SOAP
created_at                        assessment   |
                                  plan        /
                                  created_at
```

환자 동일성은 **이름 + 보호자 연락처**로 판정합니다. 연락처가 비어 있으면 이름 + 품종으로
대체 판정합니다. 같은 이름이라도 보호자 연락처가 다르면 별개 환자로 등록되므로 동명이인이
한 차트로 합쳐지지 않습니다.

`alert_memo`는 "입질 있음", "약물 알러지" 같은 진료 시 상시 노출이 필요한 경고 문구를
환자 단위로 보관합니다. 목록에서 환자명 아래에 붉게 표시됩니다.

## API

| 메서드 | 경로 | 설명 |
|---|---|---|
| `GET` | `/api/records` | 최근 진료 기록 30건. `?search=` 로 환자명 또는 연락처 검색 (최대 200건) |
| `POST` | `/api/records` | 진료 기록 생성. 환자가 없으면 자동 등록 |
| `PUT` | `/api/records/<id>` | 진료 기록 + 환자 정보 수정 |
| `DELETE` | `/api/records/<id>` | 진료 기록 삭제 |
| `GET` | `/api/patients` | 환자 목록. `?search=` 지원 |
| `GET` | `/api/patients/<id>/records` | 해당 환자의 전체 내원 이력 |

검색은 하이픈을 무시하므로 `010-1234-5678` 과 `01012345678` 이 모두 같은 결과를 냅니다.

`POST` 요청 예시:

```json
{
  "patient_name": "콩이",
  "guardian_phone": "010-1234-5678",
  "species": "토끼",
  "alert_memo": "입질 있음",
  "weight": "1.8",
  "temp": "38.5",
  "subjective": "식욕 저하 2일째",
  "objective": "체온 정상, 결막 충혈",
  "assessment": "결막염",
  "plan": "안약 3일 처방"
}
```

## 스키마 변경 시 주의

`app.py`의 `init_db()`는 백엔드가 뜰 때마다 실행됩니다. 멱등하게 작성되어 있어
(`CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT EXISTS`) 기존 데이터를 보존하지만,
운영 중인 인스턴스를 업그레이드하기 전에는 **`db_data/` 디렉터리를 먼저 백업**하십시오.

```sh
docker compose down
cp -a db_data db_data.bak.$(date +%Y%m%d)
git pull && docker compose up -d
```

`init_db()`에 `DROP TABLE`을 다시 넣지 마십시오. 과거에 그 코드가 있었고, 컨테이너가
재시작될 때마다 전체 진료 기록이 삭제됐습니다.

## 알려진 제약

- 인증이 없습니다. 신뢰할 수 있는 내부망에서만 노출하십시오.
- 백엔드가 매 기동 시 `pip install`을 수행하므로 첫 실행이 느립니다. 잦은 재시작이
  부담되면 Dockerfile로 이미지를 굽는 편이 낫습니다.
- 자동 백업이 없습니다. 진료 기록은 `db_data/` 볼륨에만 존재합니다.
