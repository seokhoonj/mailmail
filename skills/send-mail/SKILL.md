---
name: send-mail
description: 대화 중에 나온 결과·리포트·파일을 메일로 보낸다. mailrun 패키지를 호출하는 얇은 래퍼이며, 발송 전 반드시 수신자·제목·첨부를 사용자에게 확인받는다. Trigger phrases: 메일 보내줘, 메일로 보내, 메일 발송, 이거 메일로, send mail, email this, 부장님께 보내줘, 상무님께 보내줘.
---

# send-mail — 대화 결과를 메일로 보내기

메일 발송은 **되돌릴 수 없는 외부 전송**이다. 잘못 보낸 메일은 회수할 수 없고, 잘못된
수신자에게 간 첨부는 영구히 그쪽에 남는다. 그래서 이 skill의 절반은 발송 자체가 아니라
발송 전 확인이다.

## 이 skill이 하지 않는 것

로직을 갖지 않는다. MIME 조립, 차단 확장자 검사, 크기 계산, 별칭 해석은 **전부 mailrun
패키지가 한다.** 여기서 그걸 다시 구현하거나, 차단 목록을 여기에 복사해두거나, 확장자를
직접 검사하지 마라. 그러면 규칙이 두 집에 살게 되고 둘은 반드시 갈라진다. 이 skill은
`send_mail(...)` 호출 하나를 조립해서 실행할 뿐이다.

패키지 위치: `~/Dropbox/mailrun`, venv는 그 안의 `.venv`.
실행: `~/Dropbox/mailrun/.venv/bin/python`

## 절차

### 1. 무엇을 보낼지 정리한다

사용자 요청에서 다음을 뽑아낸다. 빠진 게 있으면 **추측하지 말고 묻는다.**

- `to` / `cc` / `bcc` — 주소 또는 별칭
- `subject` — 없으면 내용에서 제안하되 사용자에게 확인
- `body` — 평문 본문
- `html` — 표·서식이 필요할 때만
- `attachments` — 파일 경로
- `account` — 어느 계정으로 보낼지 (기본값은 설정의 `default_account`)

**수신자에는 설정된 기본값이 있고, 이게 함정이다.** `[defaults]`의 `to`/`cc`는 인자를
**안 넘겼을 때** 발동한다. `None`(안 적음)은 기본값을 쓰고 `()`(아무도 없음)는 기본값을
끈다.

- 사용자가 수신자를 말하지 않았다면 기본값이 맞을 때가 많다 — 다만 **2단계 확인 화면에서
  펼쳐 보여주고 승인받는다.** 기본값이라고 조용히 넘어가지 않는다.
- **사용자가 `to`만 말하고 참조를 말하지 않았다면 기본 참조가 그대로 들어간다.** "나한테
  보내줘", "테스트로 보내줘" 같은 요청에서 이건 거의 항상 틀렸다 — 상무님이 받게 된다.
  이런 경우 `cc=()`를 명시하고, 확인 화면에서 참조가 비었음을 보여준다.
- 사용자가 "참조 빼고", "나만" 이라고 하면 `cc=()`, `bcc=()`를 명시한다.

별칭이 뭐가 있는지 모르면 주소록을 먼저 읽는다:

```sh
~/Dropbox/mailrun/.venv/bin/python -c "
from mailrun import load_config
config = load_config()
print('accounts:', ', '.join(sorted(config.account_by_name)))
print('default:', config.default_account)
print('contacts:', ', '.join(sorted(config.address_book)))
"
```

### 2. 발송 전 확인받는다 — 건너뛰지 않는다

별칭은 **실제 주소로 펼쳐서** 보여준다. 사용자가 `team`이라고 말했을 때 그게 누구누구로
나가는지 눈으로 확인할 수 있어야 한다. 펼치는 것도 패키지가 한다:

```sh
~/Dropbox/mailrun/.venv/bin/python -c "
from mailrun import load_config, resolve_recipients
config = load_config()
print(resolve_recipients(['team'], address_book=config.address_book))
"
```

그 다음 이 형식으로 사용자에게 보여주고 승인을 받는다:

```
보낼 내용을 확인해 주세요:

  계정   naver (me@example.com 로 발송)
  받는이 lead@example.com (lead)
  참조   reviewer@example.com (reviewer)   <- 설정된 기본값
  제목   주간 보고
  첨부   report.xlsx (1.2 MB)

  본문:
  ---
  첨부 확인 부탁드립니다.
  ---

보낼까요?
```

참조가 비어 있으면 `참조   (없음)` 이라고 명시한다. 줄이 아예 없으면 사용자는 기본값이
들어갔는지 아닌지 알 수 없다.

승인 없이 보내지 않는다. 사용자가 "보내줘"라고 이미 말했더라도, 수신자·제목·첨부가
확정된 형태로 한 번은 보여준다 — 사용자가 승인한 것은 "메일을 보낸다"는 행위이지 아직
이 구체적 내용이 아니다.

**부장님·상무님 등 본인이 아닌 수신자에게는 절대 테스트 메일을 보내지 않는다.** 동작
확인이 목적이면 본인 주소끼리만 보낸다.

### 3. 보낸다

스크립트를 스크래치패드에 쓰고 실행한다. 본문에 줄바꿈·따옴표·한글이 섞이므로 셸
인자로 넘기지 말고 파일로 쓴다.

```python
# <scratchpad>/send.py
from mailrun import send_mail

receipt = send_mail(
    account     = "naver",
    to          = ["lead", "reviewer"],   # 별칭은 주소록에서 (1단계에서 읽은 것)
    cc          = (),                     # 기본 참조를 끈다; 생략하면 들어간다
    subject     = "Weekly report",
    body        = "Hi,\n\nThis week's report is attached.\n\nBest regards,\n",
    attachments = ["/path/to/report.xlsx"],
)
print("message-id:", receipt.message_id)
print("accepted:", receipt.accepted)
if not receipt.is_complete:
    print("REFUSED:", receipt.reason_by_refused_recipient)
```

```sh
~/Dropbox/mailrun/.venv/bin/python <scratchpad>/send.py
```

### 4. 결과를 그대로 보고한다

- `receipt.is_complete`가 참이면 발송 완료. 수신자와 message-id를 보고한다.
- 거짓이면 **일부만 갔다는 뜻이다.** 거부된 주소와 사유를 반드시 사용자에게 알린다.
  성공으로 뭉뚱그리지 않는다.

## 예외가 났을 때

패키지 예외는 이미 사용자가 읽을 수 있는 문장이다. 그대로 전달하고, 아래 조치만 덧붙인다.

**규칙: `str(err)` 를 그대로 전달한다.** 각 예외는 무엇이 잘못됐고 어떻게 고치는지를 이미
문장으로 들고 있다. 여기에 그 내용을 요약하거나 복사해두지 마라 — 한 사실이 두 집에 살면
반드시 갈라지고, 실제로 갈라진 적이 있다. 아래 표는 예외별로 **덧붙일 행동**만 적는다.

| 예외 | 덧붙일 행동 |
|---|---|
| `MissingPasswordError` | **비밀번호를 대화에 붙여넣게 하지 마라.** 본인 터미널에서 `getpass`로 입력하는 명령을 안내한다(README 참조). 한 번 넣으면 다시 물어볼 일이 없다. |
| `InsecureCredentialsError` | 없음 — 예외에 `chmod 600` 명령이 그대로 들어 있다. |
| `BlockedAttachmentError` | 링크 공유를 제안한다. |
| `EncryptedArchiveError` | 압축 비밀번호를 풀거나 링크로 보낼지 묻는다. |
| `MessageTooLargeError` | 첨부를 줄일지 묻는다. |
| `UnknownContactError` | 예외가 아는 별칭을 나열하므로, 그중에서 고르게 하거나 주소를 직접 받는다. |
| `ContactCycleError` | 없음 — 예외가 순환 경로를 그려준다. |
| `InvalidMessageError` | 수신자나 제목이 비었다. 사용자에게 받아서 다시 조립한다. |
| `RecipientRefusedError` | 주소 오타를 먼저 의심한다. |
| `AuthenticationFailedError` | 없음 — 예외에 해당 provider의 요구사항이 전부 들어 있다. |

## 첫 설정

`ConfigError`가 나면 `~/.config/mailrun/config.toml`이 없는 것이다. 형식은
`~/Dropbox/mailrun/README.md`에 있다. **설정 파일과 비밀번호는 저장소 안에 만들지 않는다** —
`~/Dropbox/mailrun`은 Dropbox로 동기화되므로 거기 둔 자격증명은 클라우드로 올라간다.
설정은 `~/.config/mailrun/config.toml`, 비밀번호는 `~/.config/mailrun/credentials.json`
(권한 600)이다. 둘 다 Dropbox 밖이다.

**비밀번호를 대화나 스크립트에 평문으로 쓰지 마라.** 대화 기록에 남는다. 사용자에게
`getpass`를 쓰는 명령을 안내하고 본인 터미널에서 실행하게 한다.
