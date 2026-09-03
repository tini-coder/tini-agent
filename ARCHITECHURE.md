# Data Structures & State Management

## Tổng quan

Codebase quản lý state theo bốn lớp, mỗi lớp có vòng đời riêng:

1. **Configuration state**: cấu hình runtime, đọc từ environment.
2. **Working state**: context tạm thời của một turn hoặc conversation.
3. **Durable state**: dữ liệu cần sống qua restart, chủ yếu trong `.tini/state.db`.
4. **Operational state**: trace, outbox, calendar artifacts và dashboard singleton.

Kiến trúc trung tâm nằm trong `tini/app.py`:

```text
gateway
  -> Tini.respond()
       -> Session.build_system()
       -> run_loop()
            -> LLM
            -> tools
       -> Session.add_exchange()
       -> memory consolidation
       -> trace
```

## 1. Các data structure chính

### `Settings`

Trong `tini/config.py`, `Settings` là một `dataclass` chứa toàn bộ cấu hình:

```python
@dataclass
class Settings:
    provider: str
    model: str
    small_model: str
    home: Path
    max_iterations: int
    history_turns: int
    consolidate_every: int
    semantic_store: str
    episodic_store: str
```

Nó được tạo một lần khi khởi động bằng `load_settings()`.

Các cấu hình quan trọng:

- `home`: mặc định `.tini`
- `history_turns`: số conversation turn tối đa được đưa vào prompt
- `max_iterations`: giới hạn vòng lặp agent
- `consolidate_every`: sau bao nhiêu exchange thì chạy consolidation
- `semantic_store`: SQLite, Supabase, Mem0, Zep hoặc LangMem
- `episodic_store`: SQLite hoặc Notion
- `graph_workflows`: bật hoặc tắt graph routing

`Settings` không phải database. Nó là snapshot cấu hình của một process. Khi đổi provider trên dashboard, agent được rebuild.

### `Session`

Trong `tini/runtime/session.py`, `Session` giữ working memory của một conversation:

```python
self.history: list[dict] = []
```

Mỗi exchange tạo hai record:

```python
{"role": "user", "content": "..."}
{"role": "assistant", "content": "..."}
```

Session còn có:

```python
self.session_id: str
self.settings
self.memory
```

`Session.history` không phải source of truth lâu dài. Nó chỉ là RAM cho conversation đang chạy.

Khi gọi `start_new()`:

```python
self.session_id = session_id
self.history = []
```

Khi gọi `switch()`:

- đổi `session_id`;
- xóa history hiện tại;
- đọc lại một phần chat cũ từ database;
- chỉ nạp `history_turns` exchange gần nhất.

Vì vậy “New chat” không xóa database. Nó chỉ tạo session ID mới, xóa working memory và giữ conversation cũ trong `chat_log`.

### `LoopResult`

Trong `tini/loop/agent.py`:

```python
@dataclass
class LoopResult:
    reply: str
    tool_calls: list[LoopEvent]
    iterations: int
    usage: dict[str, int]
```

Nó là kết quả của một agent turn:

- `reply`: câu trả lời cuối;
- `tool_calls`: các tool đã chạy;
- `iterations`: số lần gọi LLM;
- `usage`: token input/output và số LLM calls.

`LoopResult` là state tạm thời. Sau khi turn kết thúc, phần quan trọng được ghi vào `chat_log`, trace và metadata của assistant row.

### `Tool` và `ToolRegistry`

Trong `tini/tools/registry.py`:

```python
@dataclass
class Tool:
    name: str
    description: str
    input_schema: dict[str, Any]
    fn: Callable[..., str]
    wants_notify: bool = False
```

Registry dùng dictionary:

```python
self._tools: dict[str, Tool] = {}
```

Model chỉ nhìn thấy tool name, description và JSON schema. Model không sửa trực tiếp state. Nó gửi tool request, rồi `ToolRegistry.execute()` gọi Python function.

Tool trả về `str`, được nhét lại vào messages:

```python
{
    "type": "tool_result",
    "tool_use_id": call.id,
    "content": output,
}
```

Nếu tool lỗi, lỗi được chuyển thành text `Error running ...`. Model có thể quan sát lỗi và quyết định tiếp. Đây là nguyên tắc fail-soft ở tool boundary.

## 2. Durable state trong SQLite

`tini/db.py` tạo một database duy nhất:

```text
.tini/state.db
```

SQLite là source of truth cho dữ liệu local.

### `calendar_events`

Lưu event do calendar tool tạo:

```text
id
title
start
end
attendees
notes
created_at
```

Đây là artifact nghiệp vụ chính của scheduling. Apple Calendar hoặc Google Calendar chỉ là write target phụ; SQLite và ICS là state local chính.

### `facts`

Đây là semantic memory:

```text
id
subject
content
source
created_at
```

Ví dụ:

```text
subject = "alex"
content = "Alex prefers morning meetings."
source = "user"
```

`source` phân biệt:

- `user`: user nói trực tiếp;
- `consolidation`: model rút ra từ lịch sử chat.

Bảng này có FTS5 index tương ứng là `facts_fts`. Các trigger `facts_ai`, `facts_ad` và `facts_au` giữ index đồng bộ khi insert, delete hoặc update.

### `episodes`

Đây là episodic memory:

```text
id
happened_at
summary
created_at
```

Ví dụ:

```text
happened_at = "2026-08-26"
summary = "Planned the Acme demo with Alex."
```

Khác biệt:

- `facts`: điều được xem là đúng hoặc bền vững;
- `episodes`: việc đã xảy ra vào thời điểm nào.

Episodes cũng có FTS5 index là `episodes_fts`.

### `chat_log`

Đây là raw conversation log:

```text
id
role
content
consolidated
session_id
source
meta
created_at
```

Một turn bình thường tạo hai rows:

```text
user      | message
assistant | reply + metadata
```

`session_id` chỉ là label để nhóm các rows thành conversation. Không có bảng `sessions` riêng.

`meta` nằm trên assistant row và chứa JSON kiểu:

```json
{
  "gate": {},
  "graph": {},
  "iterations": 2,
  "usage": {},
  "latency_ms": 430,
  "tools": [],
  "model": "...",
  "provider": "..."
}
```

Thiết kế này cho phép dashboard mở lại conversation và vẫn biết câu trả lời được tạo như thế nào.

## 3. Ba loại memory

Trong `tini/memory/__init__.py`, facade `Memory` gom ba loại memory:

```text
semantic   facts       what Tini knows
episodic   episodes    what happened
procedural skills      how Tini should act
```

### Semantic memory

`SqliteFactStore` trong `tini/memory/semantic/store.py` cung cấp:

```python
add()
search()
list()
search_with_ids()
update()
delete()
```

Search dùng SQLite FTS5 và BM25 ranking, không dùng embedding mặc định.

Query được chuẩn hóa bởi `_fts_query()` để loại punctuation, tránh FTS operator injection, hỗ trợ Unicode, không biến `car` thành match `carpet` ngoài ý muốn và xử lý CJK/Hiragana/Katakana bằng prefix search.

Backend được thay thế qua `FactStore Protocol` trong `tini/memory/semantic/base.py`. Upstream không cần biết fact nằm ở SQLite hay hosted service:

```text
Memory
  -> FactStore Protocol
       -> SqliteFactStore
       -> SupabaseFactStore
       -> Mem0FactStore
       -> ZepFactStore
       -> LangMemFactStore
```

### Episodic memory

`SqliteEpisodeStore` trong `tini/memory/episodic/store.py`:

- search theo FTS5;
- ưu tiên rank liên quan;
- trong cùng mức liên quan thì ưu tiên `happened_at` mới hơn;
- query rỗng thì lấy các episode gần nhất.

Có thể thay bằng `NotionEpisodeStore`, nhưng interface upstream vẫn là `add()`, `search()`, `recent()`, `list()` và `delete()`.

### Procedural memory

Skills là các file `SKILL.md`, được load bởi `tini/memory/procedural/loader.py`.

Mỗi skill được parse thành:

```python
@dataclass
class Skill:
    name: str
    description: str
    body: str
    path: Path
```

Cơ chế progressive disclosure:

1. luôn scan frontmatter của mọi skill;
2. chỉ đưa body vào prompt nếu message có keyword overlap;
3. file phụ được skill tham chiếu chỉ đọc khi cần.

Procedural memory nằm trên filesystem, không nằm trong SQLite.

## 4. Retrieval state flow

Mỗi request đi qua `Session.build_system()`:

```text
load SOUL.md
  + current local time
  + model/provider identity
  + gated semantic memory
  + gated episodic memory
  + matching skills
```

Retrieval không chạy mù mỗi turn. `retrieval_gate.should_retrieve()` dùng small model quyết định:

```json
{
  "retrieve": true,
  "query": "Alex morning meetings",
  "reason": "references user's preference"
}
```

Nếu gate trả `false`, Tini không search memory.

Nếu gate lỗi:

```text
retrieve = true
query = original message
```

Đây là fail-open: thà lấy memory hơi thừa còn hơn mất memory cần thiết.

Khi retrieve:

```python
found = facts.search(query, top_k)
found += episodes.search(query, top_k=3)
```

Kết quả được format thành text và đưa vào system prompt.

## 5. Working memory bị giới hạn

Trong `_run_full_turn()` của `tini/app.py`:

```python
window = self.settings.history_turns * 2
messages = self.session.history[-window:]
messages += [{"role": "user", "content": user_message}]
```

Nếu `history_turns = 12`:

```text
12 exchange gần nhất
= 24 messages
+ user message hiện tại
```

Conversation có thể dài hàng nghìn messages trong database, nhưng prompt vẫn bị giới hạn.

```text
working memory:
    nhanh, đầy đủ vừa đủ, nằm trong RAM/prompt

durable memory:
    dài hạn, nằm trong state.db, truy hồi khi cần
```

Test trong `evals/deterministic/test_history_window.py` xác nhận turn cũ không còn xuất hiện trong prompt sau khi vượt window.

## 6. Consolidation: từ chat thành memory

Sau mỗi turn:

```python
self.memory.maybe_consolidate()
```

`consolidate_if_due()` trong `tini/memory/consolidation.py` đọc các rows:

```sql
SELECT id, role, content
FROM chat_log
WHERE consolidated = 0
ORDER BY id
```

Nếu chưa đủ `every_n` exchange thì không gọi model. Ví dụ `consolidate_every = 6` cần ít nhất 12 rows.

Khi đủ:

1. gửi raw chat cho small model;
2. model trả JSON gồm `facts` và một `episode`;
3. facts được insert vào `facts`;
4. episode được insert vào `episodes`;
5. chỉ các row đã đọc mới được đánh dấu `consolidated = 1`.

Nếu model lỗi hoặc JSON hỏng, raw log vẫn giữ `consolidated = 0`.

Invariant quan trọng:

```text
summarizer fail != lose conversation
```

Những message mới đến trong lúc summarizer chạy không bị đánh dấu nhầm là đã xử lý, vì câu lệnh update dùng chính các ID đã đọc.

## 7. Agent loop quản lý state

`run_loop()` dùng một list `messages` mutable:

```text
messages
  -> user message
  -> assistant response
  -> tool results
  -> assistant response tiếp theo
```

Mỗi iteration:

1. gọi LLM;
2. append assistant content;
3. nếu không có tool call thì kết thúc;
4. nếu có tool call thì execute;
5. append tool results;
6. lặp lại.

Pseudo-flow:

```text
while iteration <= max_iterations:
    response = LLM(messages, tools)

    messages += assistant response

    if no tool calls:
        return reply

    for each tool call:
        output = execute tool
        messages += tool result
```

`messages` chứa full state của một turn, nhưng sau khi turn xong không toàn bộ được đưa vào conversation history. Session chỉ lưu reply compact và dòng:

```text
[tools used: create_event(...) -> ...]
```

Mục đích là để turn kế tiếp biết tool đã chạy và tránh chạy lại cùng tool.

## 8. Graph state

Graph là lớp tùy chọn bao quanh loop, không thay thế loop.

Trong `tini/graph/engine.py`:

```python
state: dict
```

Mỗi node đọc snapshot state và trả về các key muốn ghi:

```python
{"calendar": "..."}
{"route": "quick"}
{"result": LoopResult(...)}
```

Engine merge output vào shared state.

### Sequential writes

Node sau có thể ghi đè key của node trước. Điều này hợp lệ.

### Parallel writes

Các node trong cùng wave chạy song song. Chúng bắt buộc ghi key khác nhau. Nếu hai node cùng ghi một key, engine ném `GraphStateCollision` để ngăn mất dữ liệu âm thầm do race condition.

### Error state

Node exception được ghi vào:

```python
state["errors"][node_name] = "..."
```

Nếu node có `on_error`, graph nhảy sang fallback. Nếu không, downstream bị drain và graph kết thúc.

### Guardrails

Graph có hai giới hạn:

- `Node.max_visits`: giới hạn số lần node chạy trong cycle;
- `max_steps`: giới hạn tổng số node execution.

Loop và graph cùng áp dụng nguyên tắc không bao giờ chạy vô hạn.

## 9. Dashboard và concurrency

Dashboard dùng một agent singleton trong `tini/ops/browser_agent.py`:

```python
_agent = None
agent_lock = threading.Lock()
_dashboard_session = None
```

Đặc điểm:

- một agent được tái sử dụng giữa các tab;
- SQLite connection dùng `check_same_thread=False`;
- `agent_lock` bảo đảm chỉ một turn chạy tại một thời điểm;
- server restart có thể resume session dashboard gần đây;
- idle quá lâu thì tạo session mới;
- đổi provider thì rebuild agent nhưng giữ nguyên `session_id`.

SQLite có:

```sql
PRAGMA busy_timeout=3000
```

để giảm lỗi database locked khi dashboard đọc trong lúc agent ghi.

State singleton chỉ thuộc dashboard process. Nó không phải source of truth. Nếu process chết, session và memory vẫn lấy lại từ SQLite.

## 10. `MEMORY.md` là gì?

Sau mỗi turn:

```python
self.memory.export_markdown()
```

File `.tini/MEMORY.md` là human-readable mirror của `facts` và `episodes`.

Source of truth vẫn là `.tini/state.db`.

Điều này cho phép con người mở và đọc memory, trong khi SQLite vẫn đảm nhiệm search, update và delete. File Markdown không được parse ngược vào runtime.

## Ví dụ: một request hoàn chỉnh

User nói:

```text
Nhắc tôi họp với Alex vào thứ Ba lúc 9 giờ sáng.
```

Luồng state:

```text
1. Gateway nhận text.

2. Tini.respond() tạo tracer context.

3. Session.build_system():
   - đọc SOUL.md
   - thêm thời gian hiện tại
   - retrieval gate quyết định có cần memory không
   - nạp skill scheduling nếu match

4. Session lấy history gần nhất theo history_turns.

5. run_loop() gửi prompt + tool schemas cho model.

6. Model gọi create_event.

7. Tool ghi calendar_events vào SQLite,
   có thể ghi thêm ICS/Apple/Google Calendar tùy config.

8. Tool output được append vào messages.

9. Model trả lời user.

10. Session.add_exchange():
    - append user/reply vào RAM
    - ghi hai rows vào chat_log
    - lưu meta JSON trên assistant row

11. maybe_consolidate():
    - nếu đủ N exchange thì distill thành facts/episodes

12. export_markdown():
    - regenerate MEMORY.md

13. tracer ghi trace và respond() trả LoopResult.
```

## Tóm tắt

```text
Settings          = cấu hình của process
Session.history   = working memory của conversation hiện tại
messages          = state đầy đủ của một agent turn
LoopResult        = kết quả của turn
Graph state       = blackboard tạm thời của workflow
chat_log          = raw durable conversation log
facts             = semantic durable memory
episodes          = episodic durable memory
SKILL.md          = procedural durable memory
MEMORY.md         = view đọc được của facts + episodes
state.db          = local source of truth
```

Thiết kế cốt lõi của repo là: **RAM giữ context ngắn và nhanh; SQLite giữ lịch sử dài; retrieval và consolidation nối hai lớp đó lại một cách có kiểm soát.**
