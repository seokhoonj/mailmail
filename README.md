# mailrun

Gmail·Naver SMTP로 메일을 보내는 파이썬 패키지. 표준 라이브러리 위에 얇게 올렸고,
런타임 의존성은 없다.

핵심은 **보내기 전에 막는 것**이다. 메일 서비스는 실행파일 첨부를 거부하고, 열어볼 수
없는 압축을 거부하고, 크기 한도를 넘긴 메시지를 거부한다. 거부는 SMTP 바운스로 돌아오는데
그건 몇 분 뒤 도착하거나 아무도 안 보는 폴더에 조용히 들어간다. mailrun은 연결을 열기
전에 그걸 전부 검사해서 호출 지점에서 예외로 터뜨린다.

## 설치

```sh
uv venv
uv pip install -e ".[dev]"
```

Python 3.11 이상. 런타임 의존성은 없고, `[dev]`는 pytest와 ruff만 가져온다.

### Dropbox 동기화에서 venv 빼기

이 저장소는 Dropbox 안에 있고 `.venv`는 50MB / 900여 파일이다. 언제든 재생성 가능한 것을
클라우드에 올릴 이유가 없으므로, Dropbox에게 무시하라고 표시해 둔다. `.venv`는 제자리에
그대로 두는 것이 낫다 — 에디터가 자동으로 인식하고 `uv`가 경로 없이 그냥 동작한다.

```sh
python3 -c "import os; os.setxattr('.venv', 'user.com.dropbox.ignored', b'1')"
dropbox filestatus .venv          # -> ignored
```

**`.venv`를 지우고 다시 만들면 이 표시가 사라진다.** 확장 속성은 디렉토리에 붙는 것이라
디렉토리와 함께 없어지고, 그러면 조용히 다시 동기화된다. 재생성했다면 위 명령을 다시 실행하고
`dropbox filestatus`로 확인할 것.

## 설정

설정은 `~/.config/mailrun/config.toml`에 둔다 — 저장소 안이 아니라. 메일 설정은 체크아웃의
속성이 아니라 머신의 속성이고, 프로젝트 디렉토리는 클라우드 드라이브로 동기화되거나 실수로
커밋되기 딱 좋은 곳이기 때문이다. `MAILRUN_CONFIG`로 다른 경로를 지정할 수 있다.

```toml
default_account = "naver"

[accounts.naver]
provider = "naver"
username = "you@naver.com"

[accounts.gmail]
provider = "gmail"
username = "you@gmail.com"

[contacts]
lead      = "lead-naver"
lead-naver = "lead@naver.com"
lead-gmail = "lead@gmail.com"
team      = ["me", "lead"]
```

**비밀번호는 설정 파일에 넣지 않는다.** 소유자만 읽을 수 있는 별도 파일
(`~/.config/mailrun/credentials.json`, 권한 600)에 들어간다. **한 번만** 넣으면 그 뒤로는
발송할 때마다 자동으로 조회된다 — 다시 묻지 않는다.

```python
from getpass import getpass
from mailrun import load_config, store_password

account = load_config().resolve_account("naver")
store_password(account, getpass(f"{account.username} 앱 비밀번호: "))
```

Gmail·Naver 모두 2단계 인증이 켜진 계정은 평문 SMTP 로그인을 거부하므로, 로그인
비밀번호가 아니라 **앱 비밀번호**를 넣어야 한다. 일회성 실행이나 컨테이너에서는
`MAILRUN_PASSWORD` 환경변수가 파일보다 우선한다.

### 왜 OS 키링이 아니라 파일인가

`.netrc`, `.pgpass`, 클라우드 CLI의 자격증명 파일이 전부 취하는 형태이고, 그들이 그 형태를
고른 이유와 같다: **프롬프트가 없다.** 스크립트·cron·에이전트 세션이 터미널과 똑같이 동작한다.

거래 조건은 분명히 해둔다. 이 파일은 암호화되지 않으므로 같은 머신의 **다른 사용자**로부터는
지켜주지만, **당신 권한으로 도는 것**으로부터는 지켜주지 못한다. OS 키링은 암호화해주지만
그건 키링이 **잠겨 있을 때만**이고, 잠긴 키링이야말로 비밀번호를 물으려 멈춰서는 그것이다.
잠금이 풀린 키링은 당신 권한으로 도는 무엇에게든 비밀을 내주므로, 결국 키링의 암호화가
파일 권한 이상으로 사주는 것은 거의 없으면서 프롬프트 비용만 남는다.

피해를 실제로 제한하는 것은 자격증명 자체의 성질이다. 여기 들어가는 것은 메일 발송으로
용도가 한정되고 계정 비밀번호를 건드리지 않고 취소할 수 있는 **앱 비밀번호**다. 그 외의 것은
여기 넣지 않는다.

파일이 소유자 외에게 읽히는 상태면 `resolve_password`는 그걸 **쓰지 않고 거부한다** —
ssh가 개인키에 대해 하는 것과 같다. `chmod 600`으로 고치라고 알려준다.

## 쓰는 법

```python
from mailrun import send_mail

send_mail(
    to          = "lead",                    # 주소, 별칭, 또는 섞어서
    subject     = "Weekly report",
    body        = "Hi,\n\nThis week's report is attached.\n\nBest regards,\n",
    attachments = ["report.xlsx"],
)
```

본문은 손대지 않고 그대로 나가므로 줄바꿈이 그대로 산다. 긴 본문은 삼중따옴표가 읽기 좋다:

```python
body = """\
Hi,

This week's report is attached. The loss ratio moved 1.2pp against last month;
the detail is on the second sheet.

Let me know if anything looks off.

Best regards,
Seokhoon
"""

send_mail(to="lead", subject="Weekly report", body=body, attachments=["report.xlsx"])
```

계정 지정, HTML 본문, 참조까지 쓰면:

```python
receipt = send_mail(
    account = "gmail",
    to      = ["lead", "analyst@example.com"],
    cc      = "team",
    bcc     = "audit@example.com",
    subject = "Monthly close",
    body    = "Hi,\n\nThe monthly close figures are below.\n\nBest regards,\n",
    html    = "<p>Hi,</p><p>The monthly close figures are <b>below</b>.</p>"
              "<p>Best regards,</p>",
)

if not receipt.is_complete:
    print(receipt.reason_by_refused_recipient)
```

`html`을 넘기면 그게 보이고, HTML을 못 읽는 클라이언트는 `body`를 본다. 둘은 같은 내용을
담아야 한다 — 다르게 쓰면 사람마다 다른 메일을 읽게 된다.

### 수신자 기본값

`[defaults]`에 `to`/`cc`를 적어두면 **인자를 안 넘겼을 때** 그게 쓰인다.

```python
send_mail(subject="Weekly report", body=body)                  # 기본 수신자·참조로
send_mail(to="me", subject="Weekly report", body=body)         # 참조는 여전히 기본값!
send_mail(to="me", cc=(), subject="Weekly report", body=body)  # 참조 없음
```

`None`(안 적음)과 `()`(아무도 없음)은 다르다. 이 구분이 없으면 기본 참조를 끌 방법이
없어진다 — 나한테만 보내는 메모에도 참조가 따라붙는다. 두 번째 줄이 그 함정이다.

여러 통을 보낼 땐 연결을 재사용한다:

```python
from mailrun import Mailer, Message, load_config

config = load_config()
with Mailer(config.resolve_account("naver")) as mailer:
    for row in rows:
        mailer.send(
            Message.compose(subject=row.subject, body=row.body, to=row.address)
        )
```

`Message.compose(...)`가 느슨한 문(門)이다 — 주소 하나면 문자열로 줘도 된다. 생성자
`Message(...)`는 엄격해서 수신자에 튜플을 요구하고 맨 문자열은 **거부한다**:

```python
Message(subject="s", body="b", to="lead@example.com")   # InvalidMessageError
Message(subject="s", body="b", to=("lead@example.com",))  # ok
Message.compose(subject="s", body="b", to="lead@example.com")  # ok
```

까다로워 보이지만 이유가 있다. 파이썬에서 `str`은 그 자체로 문자들의 이터러블이라,
받아주면 `to="lead@example.com"`이 **주소 하나가 아니라 16명의 한 글자짜리 수신자**가
된다. 그리고 dataclass의 필드 타입은 곧 `__init__`의 파라미터 타입이라(한 자리, 두 역할),
저장하는 타입과 받는 타입을 한 줄에 동시에 정직하게 적을 수가 없다. 그래서 속성은 저장하는
것을 말하고, 받아들이는 폭은 `compose`가 말한다. `send_mail`은 이미 그 폭을 정직하게 선언
하므로, 일상적으로 쓰는 문에서는 이 구분이 보이지 않는다.

## 알아둘 것

**첨부 차단은 확장자 기준이고 압축 안까지 본다.** `.exe`, `.dll`, `.jar`, `.js`, `.bat`,
`.vbs`, `.ps1`, `.msi` 등이 막히는데, `.zip`이나 `.tar.gz` **안에** 들어 있어도 똑같이
막힌다. 비밀번호 건 압축은 내용과 무관하게 거부된다 — 서비스가 스캔할 수 없기 때문이다.
Gmail은 이 목록을 공개한다([Google 고객센터](https://support.google.com/mail/answer/6590)).
Naver는 "실행파일은 제한된다"고만 공지하고 목록을 공개하지 않으므로, 같은 목록을 보수적으로
적용한다.

**`.7z`과 `.rar`은 내부를 볼 수 없다.** 표준 라이브러리에 리더가 없다. 이 두 형식 안에 든
실행파일은 검사를 통과해 서버까지 가서 거부된다.

**`.xlsx`나 `.docx`는 물리적으로 zip이지만 압축으로 취급하지 않는다.** 메일 서비스가 그렇게
하지 않기 때문이다. 판단 기준은 바이트가 아니라 확장자다.

**크기 한도는 서버가 알려주는 값을 쓴다.** 각 provider 상수(`smtp.gmail.com` 35,882,577
바이트 / `smtp.naver.com` 39,845,888 바이트)로 연결 전에 먼저 거르고, 연결한 뒤에는 서버가
EHLO에서 광고하는 `SIZE`를 다시 읽어 대조한다 — 서비스가 값을 바꿔도 코드가 따라간다.
이 숫자는 **인코딩 후** 크기다. 첨부는 base64로 실려 나가면서 약 1/3 커지므로, 원본 파일
합계로는 Gmail 약 25MiB, Naver 약 28MiB가 실질 상한이다. 웹 UI가 안내하는 "25MB"와 SMTP가
실제로 받아주는 크기는 다르다.

**Bcc는 헤더에 쓰지 않는다.** SMTP 봉투로만 전달된다. 헤더에 쓰면 숨은 참조가 서로에게
전부 보이게 된다.

**본문은 그대로 나간다.** 줄바꿈을 `<br>`로 바꾸는 따위의 손질을 하지 않는다. 서식이
필요하면 `html`을 넘긴다.

**한글 제목·본문은 별도 처리가 필요 없다.** `email.message.EmailMessage`가 RFC 2047 헤더
인코딩과 UTF-8 전송 인코딩을 알아서 한다.

## Claude Code 스킬

`skills/send-mail/`에 이 패키지를 부르는 스킬이 하나 있다. "이 결과 메일로 보내줘" 같은
요청을 받으면 `send_mail(...)` 호출 하나를 조립해 실행하고, 발송 전 수신자·제목·첨부를
확인받는다 — 메일은 되돌릴 수 없기 때문이다.

스킬은 로직을 갖지 않는다. MIME 조립도, 차단 확장자 목록도, 크기 계산도, 별칭 해석도
전부 이 패키지가 한다. 한 사실이 두 집에 살면 반드시 갈라지므로, 스킬에는 그 어느 것도
복사해 두지 않는다.

쓰려면 Claude가 찾는 자리에 심링크한다. 복사하지 않는 이유는 같다 — 사본은 갈라진다:

```sh
ln -s ~/Dropbox/mailrun/skills/send-mail ~/.claude/skills/send-mail
```

## 테스트

```sh
.venv/bin/pytest
```

전부 오프라인이고 결정적이다 — SMTP는 가짜 서버로 대체되므로 테스트가 메일을 보내지 않는다.
