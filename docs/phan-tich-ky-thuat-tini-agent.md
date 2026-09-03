# Tini-agent: đọc codebase trong 10 phút

Tini là một personal assistant local-first được cố ý giữ nhỏ và nhìn thấy được. Ý
tưởng trung tâm không phải là một framework lớn, mà là ghép vài hợp đồng đơn giản:
gateway chuyển text, session lắp context, loop gọi model và tool, memory lưu điều
bền vững, còn eval và tracing cho biết hệ thống vừa làm gì.

Bài này đi theo một request từ đầu đến cuối, sau đó tách các quyết định kỹ thuật
quan trọng ra để có thể tiếp tục đọc code theo đúng thứ tự.

## 1. Điểm vào: assembly root

Hãy bắt đầu tại [`tini/app.py`](../tini/app.py). Class `Tini` là composition root:

```text
Settings -> SQLite connection -> Memory -> ToolRegistry -> Session -> Tracer
```

`Tini.__init__()` đọc cấu hình, tạo `.tini/`, mở `state.db`, chọn LLM client,
khởi tạo facade memory, đăng ký tools, tạo session và bật tracer. `client` và
`conn` được inject từ bên ngoài, nên evals có thể dùng model giả lập và dashboard
có thể dùng connection phù hợp với server thread. Đây là một seam rất quan trọng:
core không cần biết mình đang chạy production hay test.

Các gateway chỉ gọi `respond()`. CLI ở [`tini/gateway/cli.py`](../tini/gateway/cli.py),
dashboard ở [`tini/ops/dashboard.py`](../tini/ops/dashboard.py), voice, Telegram,
Discord và WhatsApp đều là những lớp vận chuyển input/output. Chúng không sở hữu
logic agent. `GatewayAgentRunner` trong [`tini/gateway/runner.py`](../tini/gateway/runner.py)
còn bảo đảm mỗi gateway xử lý tuần tự trên một worker riêng, đóng agent và SQLite
connection đúng thread, đồng thời che secrets khi báo lỗi.

## 2. Một turn chạy như thế nào?

Đường mặc định nằm trong `Tini.respond()`:

```text
user text
  -> optional triage graph
  -> build_system()
  -> bounded history + new message
  -> run_loop()
  -> persist chat and telemetry
  -> consolidation + MEMORY.md
```

Mỗi turn được bao quanh bởi `Tracer.turn()`. Observer được compose từ observer của
gateway, tracer và callback nội bộ để thu các quyết định gate/graph. Nhờ vậy cùng
một event có thể vừa hiện trực tiếp trên UI, vừa đi vào trace, mà loop không phải
biết dashboard hay JSONL tồn tại.

### Working memory

[`tini/runtime/session.py`](../tini/runtime/session.py) xây system prompt từ ba
phần: `SOUL.md`, đồng hồ local hiện tại và thông tin model/provider. Nếu chưa có
`SOUL.md`, Tini tạo nó từ `DEFAULT_SOUL`; thay file này là thay persona và các quy
tắc hành động của agent.

Sau đó memory được hỏi qua `Memory.gated_retrieve()`. Đây không phải retrieval
đúng trên mọi request. [`tini/memory/retrieval_gate.py`](../tini/memory/retrieval_gate.py)
dùng small model trả về JSON `{retrieve, query, reason}`. Nếu gate nói không cần,
prompt không bị nhồi memory không liên quan. Nếu gate lỗi hoặc trả về output không
parse được, hệ thống **fail open**: dùng chính message làm query và retrieve. Với
memory, mất một context cũ tệ hơn việc chậm thêm một lần search.

History là sliding window. `history_turns * 2` messages gần nhất được đưa lại vào
prompt, cộng user message mới. Chat cũ không bị xóa: nó ở `chat_log`, và có thể
được consolidation hoặc retrieval gọi lại. Cách này giữ cost, latency và context
gần như bị chặn, kể cả một session Telegram chạy rất lâu.

Procedural memory là các `SKILL.md`. `SkillLoader` match skill với message và
chèn instruction phù hợp vào system prompt. Như vậy memory có ba nghĩa khác nhau:
semantic là điều đúng lâu dài, episodic là điều đã xảy ra, procedural là cách
thực hiện một loại công việc.

## 3. THE LOOP: phần lõi thật sự

[`tini/loop/agent.py`](../tini/loop/agent.py) chỉ cần một vòng lặp:

1. Gửi `system`, `messages`, `tools.schemas()` và `max_tokens` cho model.
2. Thêm assistant content vào working memory của turn.
3. Nếu không có `tool_use`, ghép text blocks thành reply và kết thúc.
4. Nếu có tool calls, thực thi từng call, phát event, thêm `tool_result` vào
   messages rồi lặp lại.

`LoopResult` gom reply, danh sách tool calls, số iteration và token usage. Có hai
điều kiện dừng: model ngừng gọi tool, hoặc `max_iterations` chặn vòng lặp vô hạn.
Đây là điểm đáng chú ý: “agent” ở đây không cần planner object, state machine
framework hay hidden callback. Lịch sử assistant/tool result của chính turn là
working memory đủ để model quyết định bước kế tiếp.

Streaming chỉ là một nhánh của cùng loop. Nếu client có `messages.stream`, text
delta được gửi cho observer; nếu streaming lỗi, code fallback về một
`messages.create()` bình thường. Gateway vì thế có thể hiển thị token live nhưng
không làm thay đổi semantics của turn.

## 4. Tools và nguyên tắc lỗi thành dữ liệu

[`tini/tools/__init__.py`](../tini/tools/__init__.py) dựng `ToolRegistry`. Mỗi
`Tool` có name, description, JSON schema và Python function. Registry chỉ expose
schema cho model; nó không để model tự chọn import hay gọi arbitrary Python.

Tool lõi gồm `create_event`, `list_events`, notes, messages và web search. Memory
còn đăng ký `manage_memory`, `update_soul`, `create_skill`. Các tích hợp có cờ
riêng: Apple tools, GitHub, MCP và experimental delegate.

`ToolRegistry.execute()` là boundary an toàn: tool không tồn tại hoặc ném exception
đều trở thành chuỗi `Error: ...` để model quan sát và quyết định tiếp. Cách này
giữ gateway sống và làm failure hiện ra trong transcript. Đổi lại, caller phải
phân biệt output lỗi với output nghiệp vụ; vì thế metadata dashboard tính status
từ output và trace lưu cả event.

Tool có thể nhận `_notify` nếu muốn stream progress. Tham số underscore không nằm
trong schema model-facing, nên đây là plumbing nội bộ chứ không phải capability
ẩn mà model tự truyền vào.

## 5. State và memory: local trước, adapter sau

[`tini/db.py`](../tini/db.py) tạo một SQLite file duy nhất: `.tini/state.db`.
Schema gồm:

- `calendar_events`: artifact của flagship task, thời gian ISO 8601.
- `facts` và `facts_fts`: semantic memory, có trigger giữ FTS5 đồng bộ.
- `episodes` và `episodes_fts`: các episode có timestamp.
- `chat_log`: raw conversation, session id, source và metadata turn.

`connect()` đặt `row_factory`, `busy_timeout=3000`, chạy schema idempotent rồi
migrate thêm column còn thiếu. Dashboard có thể dùng `check_same_thread=False`,
nhưng connection dùng chung phải được bảo vệ ở tầng runner/lock.

Facade [`tini/memory/__init__.py`](../tini/memory/__init__.py) giữ cùng API dù backend
semantic là SQLite, Supabase, mem0, Zep hay LangMem; episodic là SQLite hoặc Notion.
Hợp đồng nằm ở [`tini/memory/semantic/base.py`](../tini/memory/semantic/base.py),
với các phép `add`, `search`, `list`, `search_with_ids`, `update`, `delete` và
`settle`. Bộ conformance eval chạy cùng contract trên các backend để việc đổi nơi
lưu không biến thành lỗi dashboard im lặng.

SQLite semantic dùng FTS5 BM25 thay vì embedding. `_fts_query()` chuẩn hóa input
thành các token `OR`, xử lý dấu tiếng Việt và prefix cho CJK vì tokenizer
`unicode61` không tách các chuỗi chữ Hán/Nhật giống Latin. Đây là chi tiết nhỏ
nhưng rất quan trọng: retrieval sai có thể đưa một episode không liên quan vào
prompt và tạo ra câu trả lời “tự tin nhưng sai”.

Cuối turn, `log_chat()` ghi user row và assistant row. `maybe_consolidate()` sau
mỗi N exchange gọi small model để chắt lọc fact/episode mới. `export_markdown()`
tạo `.tini/MEMORY.md` như view đọc được bằng mắt; database vẫn là source of truth,
file Markdown chỉ là generated mirror.

## 6. Graph: thêm hình dạng quanh loop

Graph không thay thế loop. Nó giải quyết loại bài toán khác: khi hình dạng đã biết
trước thì các bước độc lập nên chạy song song và đường đi nên được trace rõ.

Engine ở [`tini/graph/engine.py`](../tini/graph/engine.py) có ba khái niệm:

- `state`: một dict kiểu blackboard; node đọc snapshot và trả các key cần merge.
- edge/router: edge tĩnh là dependency, router là Python function chọn target.
- wave/guard: node ready chạy trong cùng wave; `max_visits` và `max_steps` chặn
  cycle.

Parallel nodes bắt buộc ghi các key rời nhau. Nếu hai node cùng ghi một key trong
một wave, `GraphStateCollision` được raise thay vì âm thầm ghi đè. Event schema
gồm `graph_start`, `node_start`, `node_end`, `route`, `graph_end`; dashboard vẽ
topology từ `Graph.describe()` chứ không dùng một sơ đồ copy bằng tay.

Workflow `triage` chạy classifier small model và đọc calendar song song, chờ cả hai,
rồi route `quick_reply` hoặc `full_agent`. Nhánh full gọi lại `_run_full_turn()`
của Tini, tức graph node dùng đúng THE LOOP; flag graph tắt thì path cũ giữ nguyên.
Classifier lỗi, graph lỗi hoặc route không rõ đều fail open về loop thường.

Workflow `gather` là ví dụ graph “đúng bài”: scan GitHub, web, calendar và memory
không phụ thuộc nhau, sau đó một lần synthesize và có thể draft digest. Nó **chỉ đề
xuất, không hành động**. Model synthesize không được nhận `tools=`; test còn đọc
AST/source để ngăn ai đó vô tình thêm `run_loop`, `ToolRegistry` hoặc tool write vào
workflow này. Router dựa trên số PR/issue/event, không dựa trên prose của model.

Ngược lại, một request kiểu “đọc diff, chạy test, nếu fail thì sửa rồi thử lại” có
hình dạng không biết trước. Loop là abstraction phù hợp hơn graph trong trường hợp
đó. Đây là trade-off cốt lõi của repo: graph cho known structure, loop cho
discovery.

## 7. Provider và cấu hình

[`tini/config.py`](../tini/config.py) dùng một dataclass `Settings`, toàn bộ knob là
environment variable. `TINI_HOME` quyết định nơi lưu state; các cờ như
`TINI_GRAPH_WORKFLOWS`, `TINI_EXPERIMENTAL`, `TINI_GH_TOOL`, calendar và gateway
đều opt-in.

[`tini/loop/models.py`](../tini/loop/models.py) làm adapter provider. Loop nói một
dialect Anthropic Messages; provider được chia thành Anthropic-wire và
OpenAI-wire. Bảng `PROVIDERS` chứa key env, endpoint, model chính và small model.
`get_client()` điền default model, validate key và tránh mang nhầm model của
provider trước sang provider mới. Vì model ids chỉ là string, thêm provider không
đòi hỏi sửa loop.

Có một đánh đổi rõ: mọi tool registered được đưa vào prompt của model active.
Vì vậy những capability nhạy cảm bị đặt sau feature flag thay vì đăng ký mặc định.
MCP cũng là opt-in và bridge được đóng trong `Tini.close()`.

## 8. Quan sát và kiểm thử

[`tini/ops/tracing.py`](../tini/ops/tracing.py) ghi JSONL theo ngày vào
`.tini/traces/`, không cần dependency. Nếu có `OTEL_EXPORTER_OTLP_ENDPOINT`, cùng
event đó được xuất thành OpenTelemetry spans. Ngoài trace, token usage đi vào
`usage.jsonl` như ledger lâu dài; trace có thể reset cho demo nhưng usage không nên
bị xóa.

Test được chia thành hai tầng:

- `evals/deterministic/`: pytest offline, trả lời 0/1. Chúng kiểm tra tool artifact,
  history window, FTS, graph concurrency, error semantics, topology và các feature
  flag.
- `evals/judge/`: gọi model để chấm chất lượng, chỉ chạy khi provider có key.

`make gate` chạy deterministic trước. Chỉ khi tầng này pass mới chạy judge; thiếu
key thì judge được ghi là skipped nhưng deterministic vẫn phải pass. Vì vậy gate
không biến một lỗi logic thành “đạt” chỉ vì model judge không khả dụng.

Các lệnh đọc code và kiểm chứng nhanh:

```bash
make lint
make eval
make gate
sqlite3 .tini/state.db '.tables'
```

Dashboard có các tab cho loop, graph, memory, tools, database và ops. Đây không
phải một UI tách rời: các card lấy metadata từ chat log, topology lấy từ graph
object, còn trace lấy từ event stream thật.

## 9. Kết luận kiến trúc

Điểm mạnh nhất của Tini là các boundary nhỏ và có thể kiểm chứng:

```text
gateway -> Tini.respond -> Session/Memory -> Loop -> ToolRegistry
                                      \-> Graph (optional)
reply -> chat_log + consolidation + MEMORY.md
events -> observer -> UI + JSONL + OpenTelemetry
code changes -> deterministic eval -> judge -> release gate
```

Hệ thống không cố giải quyết mọi loại agent bằng một abstraction. Nó giữ loop làm
trung tâm cho hành động mở, thêm graph khi cần cấu trúc đã biết, giữ memory local
và queryable, rồi ghi lại đủ event để một người có thể mở file và biết chuyện gì
đã xảy ra. Đó là lý do codebase phù hợp để học: mỗi “hộp” trên kiến trúc tương ứng
với một module Python, một hợp đồng nhỏ và một nhóm test cụ thể.