# mailrun

Gmail·Naver SMTP로 메일을 보내는 파이썬 패키지. 표준 라이브러리 위에 얇게 올렸고,
런타임 의존성은 없다.

핵심은 **보내기 전에 막는 것**이다. 메일 서비스는 실행파일 첨부를 거부하고, 열어볼 수 없는
압축을 거부하고, 크기 한도를 넘긴 메시지를 거부한다. 거부는 SMTP 바운스로 돌아온다. 몇 분
뒤에야 도착하거나, 아무도 안 보는 폴더에 조용히 들어간다. mailrun은 연결을 열기 전에 이걸
전부 검사해서 호출 지점에서 예외로 터뜨린다.

## 설치

```sh
uv venv
uv pip install -e ".[dev]"
```

Python 3.11 이상. 런타임 의존성은 없고, `[dev]`는 pytest·ruff·mypy만 가져온다.

## 설정

설정은 저장소 안이 아니라 `~/.config/mailrun/config.toml`에 둔다. 메일 설정은 작업 사본이
아니라 머신에 딸린 것이고, 프로젝트 디렉토리는 클라우드 드라이브로 동기화되거나 실수로
커밋되기 딱 좋기 때문이다. `MAILRUN_CONFIG`로 다른 경로를 지정할 수 있다.

```toml
default_account = "naver"

[accounts.naver]
provider = "naver"
username = "you@naver.com"

[accounts.gmail]
provider = "gmail"
username = "you@gmail.com"

[contacts]
me         = "you@naver.com"
lead       = "lead-naver"
lead-naver = "lead@naver.com"
lead-gmail = "lead@gmail.com"
team       = ["me", "lead"]
```

별칭은 주소 하나를 가리키거나, 다른 별칭들을 묶는다. `team`처럼 별칭이 별칭을 품어도 된다.

**비밀번호는 설정 파일에 넣지 않는다.** 소유자만 읽을 수 있는 별도 파일
(`~/.config/mailrun/credentials.json`, 권한 600)에 들어간다. **한 번만** 넣으면 그 뒤로는
발송할 때마다 자동으로 조회된다. 다시 묻지 않는다.

```python
from getpass import getpass
from mailrun import load_config, store_password

account = load_config().resolve_account("naver")
store_password(account, getpass(f"{account.username} 앱 비밀번호: "))
```

일회성 실행이나 컨테이너에서는 `MAILRUN_PASSWORD` 환경변수가 파일보다 우선한다. 파일 위치
자체는 `MAILRUN_CREDENTIALS`로 옮길 수 있고, 지정이 없으면 설정과 자격증명 둘 다
`XDG_CONFIG_HOME`을 따른다.

**이 파일은 암호화되지 않는다.** `.netrc`나 `.pgpass`와 같은 형태다. 같은 머신의 다른
사용자는 파일 권한으로 막지만, 내 권한으로 도는 프로그램은 막지 못한다. 그래서 여기 들어가는
것은 **앱 비밀번호뿐이다.** 메일 발송으로 용도가 한정되고, 계정 비밀번호를 건드리지 않고
취소할 수 있다. 그 외의 것은 넣지 않는다.

소유자 말고 다른 사용자도 읽을 수 있는 상태면 `resolve_password`는 그 파일을 **읽지 않고
거부하며**, `chmod 600` 명령을 알려준다. ssh가 개인키에 하는 것과 같다. 단
`MAILRUN_PASSWORD`가 설정돼 있으면 파일을 아예 읽지 않으므로 이 검사도 건너뛴다.

### 앱 비밀번호 발급

**로그인 비밀번호는 두 서비스 다 거부한다.** 반드시 앱 비밀번호를 따로 발급해야 하고, 둘의
형식이 정반대라 헷갈리기 쉽다.

| | 자릿수 | 형태 | 2단계 인증 |
|---|---|---|---|
| **Naver** | **12자리** | **대문자 + 숫자** | 필수 |
| **Gmail** | **16자리** | **소문자** | 필수 |

#### Naver

2025년 6월 24일부터 POP3/IMAP/SMTP 접속에 2단계 인증과 앱 비밀번호가 필수가 되었다. 그 전에
만든 설정이 갑자기 안 되기 시작했다면 그 때문이다.

1. **네이버ID → 보안설정 → 2단계 인증**을 켠다. 이게 없으면 다음 단계의 메뉴가 없다.
2. 같은 화면에서 **애플리케이션 비밀번호 → 생성하기**를 누른다.
   - **종류선택은 이름일 뿐이다.** 아웃룩·아이폰·지메일은 흔한 예시고, 무엇을 고르든 발급되는
     비밀번호는 같다. **직접 입력**에 mailrun이라고 적으면 나중에 알아보기 좋다.
   - 12자리 대문자+숫자가 나온다. **그 화면을 벗어나면 다시 못 본다.**
3. **메일 → 환경설정 → POP3/IMAP 설정**에서 **SMTP 사용함**을 확인한다. 이미 켜져 있어도
   **사용 안 함 → 저장 → 사용함 → 저장**으로 한 번 껐다 켜야 2025-06 정책 변경이 반영된다.

틀렸을 때 서버가 주는 답:

```
535 5.7.1 Username and Password not accepted ... - nsmtp
```

이 메시지는 "비밀번호가 틀렸다"와 "SMTP가 꺼져 있다"를 구분해주지 않는다. 둘 다 확인한다.

#### Gmail

1. **2단계 인증을 먼저 켠다.** 켜지 않으면 앱 비밀번호 메뉴 자체가 나타나지 않는다.
2. myaccount.google.com/apppasswords 에서 발급한다.
3. 16자리 소문자가 네 칸씩 띄어서 표시된다. 공백은 있든 없든 상관없다.

틀렸을 때 서버가 주는 답. 이쪽은 친절하다:

```
534 5.7.9 Application-specific password required
```

#### 넣을 때 흔한 실수

`getpass`는 입력을 화면에 보여주지 않는다. **두 번 붙여넣어도 알 수가 없다.** 자릿수를
확인하면서 넣는다.

```sh
.venv/bin/python -c "
from getpass import getpass
from mailrun import load_config, store_password

account = load_config().resolve_account('naver')       # 또는 'gmail'
password = getpass(f'{account.username} 앱 비밀번호: ').strip()
print(f'  입력된 것: {len(password)}자')                # naver 12, gmail 16
store_password(account, password)
print('  저장 완료 — 다시 묻지 않는다')
"
```

제대로 들어갔는지는 보내보지 않고도 확인할 수 있다.

```sh
.venv/bin/python -c "
import smtplib
from mailrun import load_config, resolve_password

for name in ('naver', 'gmail'):
    account = load_config().resolve_account(name)
    smtp = smtplib.SMTP(account.provider.smtp_host, account.provider.smtp_port, timeout=20)
    smtp.ehlo(); smtp.starttls(); smtp.ehlo()
    try:
        smtp.login(account.username, resolve_password(account))
        print(f'  {name}: OK')
    except smtplib.SMTPAuthenticationError as e:
        print(f'  {name}: {e.smtp_code} {e.smtp_error.decode()[:60]}')
    smtp.quit()
"
```

## 사용법

```python
from mailrun import send_mail

send_mail(
    to          = "lead",                    # 주소, 별칭, 또는 섞어서
    subject     = "Weekly report",
    body        = "Hi,\n\nThis week's report is attached.\n\nBest regards,\n",
    attachments = ["report.xlsx"],
)
```

본문은 손대지 않고 그대로 나가므로 줄바꿈이 그대로 산다. 긴 본문은 삼중따옴표가 읽기 좋다.

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
담아야 한다. 다르게 쓰면 사람마다 다른 메일을 읽게 된다.

### 수신자는 항상 부른 자리에서 정한다

기본 수신자 같은 건 없다. `to`는 필수이고, 적지 않은 것은 안 들어간다.

```python
send_mail(to="me", subject="Weekly report", body=body)   # 참조 없음. 나한테만 간다
```

봉투에 오른 주소는 전부 이 호출에 적힌 것이다. 설정 파일이 뒤에서 참조를 하나 더 붙이는
일은 없다.

### 여러 통 보내기

연결을 재사용한다.

```python
from mailrun import Mailer, Message, load_config

config = load_config()
with Mailer(config.resolve_account("naver")) as mailer:
    for row in rows:
        mailer.send(
            Message.compose(subject=row.subject, body=row.body, to=row.address)
        )
```

**`Message`는 별칭을 풀지 않는다.** 별칭 해석은 `send_mail`에만 있다. `Mailer`로 직접 보낼
때는 실제 주소를 주거나, 먼저 풀어서 넘긴다.

```python
from mailrun import resolve_recipients

addresses = resolve_recipients("team", address_book=config.address_book)
mailer.send(Message.compose(subject="s", body=body, to=addresses))
```

풀지 않고 `to="team"`이라고 주면 예외 없이 통과해서 `team`이라는 글자가 그대로 서버로 간다.
바운스로 알게 되는 몇 안 되는 자리다.

### Message.compose와 생성자

`Message.compose(...)`는 느슨하게 받는다. 주소가 하나면 문자열로 줘도 된다. 생성자
`Message(...)`는 엄격하다. 수신자를 튜플로만 받고 단독 문자열은 **거부한다.**

```python
Message(subject="s", body="b", to="lead@example.com")          # InvalidMessageError
Message(subject="s", body="b", to=("lead@example.com",))       # ok
Message.compose(subject="s", body="b", to="lead@example.com")  # ok
```

파이썬에서 문자열은 `for`로 돌리면 한 글자씩 나온다. 수신자 자리에서 문자열을 그대로
받아주면 `to="lead@example.com"`은 주소 하나가 아니라 **16명의 한 글자짜리 수신자**가 된다.
그래서 생성자는 거부하고, 문자열 지름길은 `compose`에 뒀다. `send_mail`도 문자열을
받아주므로, 평소 쓰는 자리에서는 이 구분이 드러나지 않는다.

## 발송 전 검사

첨부 차단은 확장자 기준이고 압축 안까지 본다. `.exe`, `.dll`, `.jar`, `.js`, `.bat`, `.vbs`,
`.ps1`, `.msi` 등이 막히는데, `.zip`이나 `.tar.gz` 안에 들어 있어도 똑같이 막힌다. Gmail은 이
목록을 공개한다([Google 고객센터](https://support.google.com/mail/answer/6590)). Naver는
"실행파일은 제한된다"고만 공지하고 목록을 공개하지 않으므로, 같은 목록을 보수적으로 적용한다.
이 목록은 구글이 게시한 것을 읽어둔 스냅샷이라 서비스 정책이 바뀌면 달라질 수 있다.

비밀번호가 걸린 **zip**은 내용과 무관하게 거부된다. 서비스가 스캔할 수 없기 때문이다.

중첩은 4겹까지, 중첩된 멤버는 64MiB까지 들여다본다. 그보다 깊거나 큰 것은 끝까지 스캔할 수
없다는 이유로 거부된다.

### 검사가 못 보는 것

**`.7z`과 `.rar`은 내부를 볼 수 없다.** 표준 라이브러리에는 이 형식을 읽는 기능이 없다. 이 두
형식 안에 든 실행파일은 검사를 통과해 서버까지 가서 거부된다. 암호가 걸려 있어도 마찬가지로
통과한다. 열어보지를 못하니 암호가 걸렸는지도 모른다.

**`.xlsx`나 `.docx`는 속을 열어보면 zip이지만 압축으로 취급하지 않는다.** 메일 서비스가
압축으로 취급하지 않기 때문이다. 판단 기준은 바이트가 아니라 확장자다.

### 크기 한도

크기 한도는 서버가 알려주는 값을 쓴다. 연결 전에는 메일 서비스별 상수(`smtp.gmail.com`
35,882,577 바이트 / `smtp.naver.com` 39,845,888 바이트)로 먼저 거른다. 연결한 뒤에는 서버가
EHLO에서 알려주는 `SIZE`를 다시 읽어 대조한다. 그래서 서비스가 값을 바꿔도 코드가 따라간다.

이 숫자는 **인코딩 후** 크기다. 첨부는 base64로 실려 나가면서 약 37% 커지므로, 원본 파일
합계로는 Gmail 약 25MiB, Naver 약 28MiB가 실질 상한이다. 웹 UI가 안내하는 "25MB"와 SMTP가
실제로 받아주는 크기는 다르다.

### Bcc와 본문

Bcc는 헤더에 쓰지 않는다. SMTP 봉투로만 전달된다. 헤더에 쓰면 숨은 참조가 서로에게 전부
보이게 된다.

본문은 그대로 나간다. 줄바꿈을 `<br>`로 바꾸는 따위의 손질을 하지 않는다. 서식이 필요하면
`html`을 넘긴다.

한글 제목·본문은 별도 처리가 필요 없다. `email.message.EmailMessage`가 RFC 2047 헤더
인코딩과 UTF-8 전송 인코딩을 알아서 한다.

## Claude Code 스킬

`skills/send-mail/`에 Claude Code용 스킬이 있다. 자체 로직은 없고 이 패키지의
`send_mail(...)`을 호출하기만 한다.

말할 때 **무엇을 본문에, 무엇을 첨부로, 누구에게** 를 짚어주면 그대로 조립된다.

> 6월 손익 요약해서 표로 만들고, balance.xlsx 첨부해서 lead 한테 보내줘.

짚지 않은 것은 묻는다. 추측하지 않는다.

"이거 메일로 보내줘"라고만 해도 위험하지는 않다. 어느 쪽이든 발송 전에 수신자·제목·첨부를
별칭까지 펼쳐서 보여주고 승인을 받기 때문이다. 메일은 되돌릴 수 없다. 짚어주면 확인 화면에서
고칠 게 줄어들 뿐이고, 안전은 문구가 아니라 그 확인 화면이 만든다.

쓰려면 Claude가 찾는 자리에 심볼릭 링크를 건다. 사본을 두지 않는 이유는 사본이 갈라지기
때문이다.

```sh
ln -s "$PWD/skills/send-mail" ~/.claude/skills/send-mail
```

## 테스트

```sh
.venv/bin/pytest              # 전부 오프라인·결정론적
.venv/bin/ruff check src tests scripts
.venv/bin/mypy                # src는 --strict, 테스트는 타입 표기만 면제
```

테스트는 SMTP를 가짜 서버로 대체하므로 **메일을 보내지 않는다.** CI
(`.github/workflows/check.yml`)는 Python 3.11과 3.13에서 같은 검사를 돌린다. 추가로 빈 환경에
설치했을 때 서드파티 없이 import 되는지도 확인한다.

차단 확장자 목록은 서버에 물어볼 수가 없어서, 구글 페이지와 직접 대조하는 스크립트를 둔다.
CI가 매주 월요일에 돌린다.

```sh
.venv/bin/python scripts/check_blocked_list.py    # 다르면 exit 1
```
