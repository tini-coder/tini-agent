# Context Architecture

## Tổng quan

Codebase áp dụng context engineering theo hướng:

> **Context = system instructions + memory liên quan + lịch sử gần đây + tool schemas + kết quả tool + state của workflow**

Trung tâm là [tini/runtime/session.py](tini/runtime/session.py#L63), còn vòng lặp thực thi nằm ở [tini/loop/agent.py](tini/loop/agent.py#L47).

Kiến trúc tách context thành ba lớp chính:

- **Working memory:** context ngắn hạn của lượt chat hiện tại.
- **Durable memory:** facts, episodes và raw chat được lưu lâu dài.
- **Procedural memory:** các `SKILL.md` mô tả cách hành động.

## 1. Build context cho chat thông thường

Luồng chính:

```text
user message
    |
    v
build_system()
    |- SOUL.md / persona
    |- current local time + timezone
    |- model/provider metadata
    |- gated semantic memory
    `- matched SKILL.md
    |
    v
history window gần nhất
    |
    v
user message mới
    |
    v
run_loop()
    |- tool schemas
    |- assistant response
    |- tool calls
    `- tool results
```

### System context

`Session.build_system()` ghép:

- Persona và behavioral rules từ `SOUL.md`.
- Thời gian hiện tại, timezone và UTC offset.
- Model và provider đang chạy.
- Semantic memory và episodic memory nếu retrieval gate cho phép.
- Tối đa hai skill phù hợp với message.

`SOUL.md` là persona/procedural memory có thể chỉnh sửa trực tiếp, thay vì toàn bộ behavior bị hard-code trong Python.

### Conversation context

[Tini app](tini/app.py#L125) không gửi toàn bộ lịch sử. Mỗi lượt chỉ đưa vào prompt:

```text
last history_turns * 2 messages + user message hiện tại
```

Mặc định `history_turns = 12`, tức tối đa 24 message cũ. Đây là sliding window giúp chi phí và latency không tăng vô hạn.

Các turn cũ không bị xóa:

- raw chat lưu trong `chat_log`
- facts lưu trong `facts`
- episodes lưu trong `episodes`
- khi cần sẽ được retrieval gate tìm lại

Test cho invariant này nằm ở [evals/deterministic/test_history_window.py](evals/deterministic/test_history_window.py#L13).

## 2. Retrieval và memory selection

### Retrieval gate

Trước khi search memory, một small model quyết định:

```json
{
  "retrieve": true,
  "query": "Alex meeting",
  "reason": "asks about a person"
}
```

Logic nằm ở [tini/memory/retrieval_gate.py](tini/memory/retrieval_gate.py#L34).

Gate có ba mục đích:

- Không search memory cho toán học, small talk hoặc kiến thức chung.
- Giảm latency và token.
- Tránh memory không liên quan làm bias câu trả lời.

Nếu gate lỗi, hệ thống **fail open** và vẫn retrieve. Lý do là mất memory được xem là tệ hơn việc dùng memory cũ.

`Memory.gated_retrieve()` kết hợp:

- semantic facts: top-k, mặc định 4
- episodic memories: top-k 3

Implementation ở [tini/memory/__init__.py](tini/memory/__init__.py#L88).

### Search strategy

SQLite mặc định dùng FTS5 keyword search, không dùng embedding. [tini/memory/semantic/store.py](tini/memory/semantic/store.py#L52) xử lý:

- token hóa input
- escaping query cho FTS
- Unicode và diacritics
- prefix search cho CJK/non-segmented scripts
- giới hạn top-k

Codebase có regression test chống query Unicode rỗng khiến hệ thống trả về episode gần nhất nhưng không liên quan: [evals/deterministic/test_memory_search.py](evals/deterministic/test_memory_search.py#L95).

Semantic backend có thể thay thế bằng Supabase, Mem0, Zep hoặc LangMem nhưng abstraction phía trên vẫn giữ nguyên.

## 3. Procedural context từ `SKILL.md`

Skill loader triển khai progressive disclosure:

1. Scan frontmatter của mọi skill.
2. Match skill dựa trên keyword overlap.
3. Chỉ đưa body của skill phù hợp vào system prompt.
4. Tối đa hai skill mỗi turn.

Implementation ở [tini/memory/procedural/loader.py](tini/memory/procedural/loader.py#L45).

Điều này giúp:

- Không nhồi toàn bộ skill vào mọi prompt.
- Tách “cách hành động” khỏi persona và facts.
- Skill mới hoặc skill bị sửa có hiệu lực ở turn kế tiếp nhờ tự-rescan.
- Skill có thể nằm trong repo hoặc trong `TINI_HOME/skills`.

Hạn chế hiện tại: matching chỉ dựa trên keyword overlap, không semantic matching. Synonym, ngôn ngữ khác hoặc câu diễn đạt gián tiếp có thể không kích hoạt được skill.

## 4. Tool context và agent loop

Tool context gồm hai phần:

- tool descriptions và JSON schemas được gửi qua `tools=`
- tool outputs được append vào message history

Trong [tini/loop/agent.py](tini/loop/agent.py#L57), mỗi iteration:

1. Model nhận system, history và tool schemas.
2. Model trả text hoặc tool call.
3. Tool được execute.
4. Kết quả tool trở lại context dưới dạng `tool_result`.
5. Loop tiếp tục đến khi model dừng.

Có hai guardrail:

- Không còn tool call thì kết thúc turn.
- Đạt `max_iterations` thì dừng, mặc định 10.

`ToolRegistry.execute()` không làm crash toàn bộ loop khi tool lỗi. Nó trả lỗi dạng text để model quan sát và xử lý tiếp: [tini/tools/registry.py](tini/tools/registry.py#L43).

Sau turn, tool activity được nén thành dòng:

```text
[tools used: create_event(...) -> ...]
```

Dòng này được lưu vào history dài hạn để turn sau biết tool nào đã chạy, tránh gọi lại cùng tool. Test nằm ở [evals/deterministic/test_tool_trigger.py](evals/deterministic/test_tool_trigger.py#L70).

## 5. Các context build đặc biệt

### Graph triage context

Khi bật graph workflows:

```text
message
  |- classifier context: chỉ user message
  `- calendar context: today's calendar
          |
          v
       router
       |- quick reply: small model + calendar
       `- full agent: context build thông thường
```

Ở [tini/graph/workflows/triage.py](tini/graph/workflows/triage.py#L47):

- classifier chỉ quyết định `quick` hoặc `full`
- router là code, không phải model
- message cần task, memory hoặc tool sẽ vào full loop
- small talk tránh đánh thức model lớn
- lỗi graph fail open về plain loop

Quick path không sử dụng đầy đủ `SOUL.md`, memory và tool schemas. Đây là tối ưu có chủ đích, nhưng context của quick reply nghèo hơn full path.

### Gather workflow context

Morning gather tạo context từ bốn nguồn chạy song song:

- GitHub
- web
- calendar
- memory

Sau đó một model duy nhất tổng hợp digest. Workflow nằm ở [tini/graph/workflows/gather.py](tini/graph/workflows/gather.py#L75).

Đặc điểm context engineering:

- Context được tạo trước từ các scan xác định.
- Không cho model tool schemas.
- Model chỉ draft proposal, không thể hành động.
- Mỗi nhánh có fallback text nếu lỗi.
- Router dựa trên số lượng PR, issue và event, không dựa vào prose của model.

Đây là structured context assembly, khác với loop tự khám phá tool từng bước.

### Delegated sub-agent context

`delegate_task` truyền cho `pi`:

- task tự-contained
- working directory
- model/provider
- project extensions và skills
- transcript riêng

Sub-agent không nhận nguyên vẹn conversation context của Tini. Nó được coi là một context boundary mới. Điều này giảm coupling, nhưng model chính phải viết task đủ rõ để sub-agent làm việc đúng.

## 6. Context persistence và consolidation

Sau mỗi turn, chat được lưu vào `chat_log`. Cứ đủ `N` exchanges, consolidation model đọc các row chưa xử lý và tạo:

- durable facts
- một episodic summary

Implementation ở [tini/memory/consolidation.py](tini/memory/consolidation.py#L37).

Các invariant đã được test:

- dưới threshold không gọi summarizer
- chỉ đọc row chưa consolidate
- lỗi thì không đánh dấu dữ liệu đã xử lý
- message đến trong lúc consolidate không bị nuốt
- conversation không có fact vẫn được đánh dấu hoàn tất

Đây là cơ chế chuyển từ short-term context sang long-term context.

## 7. Provider và context portability

Loop sử dụng Anthropic-shaped message contract. Với provider OpenAI-compatible, [tini/loop/models.py](tini/loop/models.py#L308) chuyển đổi:

- system message
- text messages
- assistant tool calls
- tool results
- usage metadata

Adapter còn giữ `thought_signature` cho một số reasoning model cần nó ở lượt tool tiếp theo. Vì vậy context semantics được giữ ổn định giữa các provider thay vì mỗi provider có một loop riêng.

## 8. Observability của context

Mỗi turn lưu metadata như:

- retrieval gate decision
- graph route
- số iteration
- input/output token usage
- latency
- tool status
- model/provider

Metadata được lưu cùng assistant row và trace JSONL. Nhờ vậy có thể phân tích không chỉ câu trả lời, mà cả context path đã tạo ra câu trả lời đó.

## 9. Các điểm còn thiếu hoặc có rủi ro

### 9.1 Chưa có token budget thật cho toàn bộ system context

`history_turns` giới hạn số message, nhưng memory, skill body và tool output có thể rất dài. Chưa thấy cơ chế đo hoặc cắt theo tổng token.

### 9.2 Retrieval gate vẫn tốn thêm một model call

Một lượt full loop có thể tốn gate call, model call chính và nhiều tool iterations. Đây là tradeoff hợp lý, nhưng chưa có adaptive caching cho các message tương tự.

### 9.3 Skill matching còn nông

Keyword overlap dễ bỏ sót intent và chưa có ranking theo độ tin cậy, version hoặc conflict giữa các skill.

### 9.4 Consolidation đọc log chưa consolidate trên toàn hệ thống

Logic không lọc theo `session_id`. Đây có thể là chủ ý để memory xuyên các conversation, nhưng cũng có nguy cơ trộn context giữa những session không liên quan.

### 9.5 Persistent history chỉ giữ tool summary

Turn sau biết tool nào đã chạy và output rút gọn, nhưng không giữ nguyên cấu trúc đầy đủ của tool interaction trong persistent conversation context.

### 9.6 Chưa có lớp bảo vệ rõ ràng cho instruction injection

`SOUL.md`, skill và memory có thể được user hoặc agent tạo ra rồi đưa vào system context. Nếu dữ liệu chứa chỉ dẫn ngoài ý muốn, nó có thể ảnh hưởng behavior của model.

## Kết luận

Codebase đã xử lý khá đầy đủ các nền tảng của context engineering:

- bounded context
- retrieval gating
- progressive disclosure
- memory consolidation
- tool-loop context
- context isolation giữa workflow và sub-agent
- fail-open behavior
- provider-independent message contract
- context-path observability

Các phần nên ưu tiên nếu muốn nâng cấp tiếp:

1. token-level budgeting và context compaction
2. semantic skill retrieval
3. memory provenance và conflict resolution
4. kiểm soát instruction injection trong dữ liệu được retrieve
5. đánh giá chất lượng context assembly độc lập với chất lượng model
