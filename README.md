# ASIL-D Test Orchestrator v3.0

Hệ thống điều phối kiểm thử tự động hỗ trợ tiêu chuẩn **ISO 26262 ASIL-D** cho các dự án Java. Công cụ này sử dụng AI và phân tích đồ thị mã nguồn để tối ưu hóa việc tạo test case, phân tích MC/DC và đảm bảo an toàn phần mềm.

## 🚀 Tính năng chính

- **Phân tích mã nguồn bằng Đồ thị:** Chuyển đổi mã Java thành AST, CFG, và DFG để AI hiểu sâu về logic.
- **Phân tích Ngữ nghĩa (Semantic Analysis):** Tự động nhận diện mức độ ASIL (A-D) và các thành phần quan trọng về an toàn (Safety-Critical).
- **Đánh giá rủi ro (Risk Scoring):** Ưu tiên kiểm thử các vùng mã nguồn có rủi ro cao nhất.
- **Đa đại lý AI (Multi-Agent):** Các Agent chuyên biệt cho Safety, Unit Test, MC/DC và Mutation Testing.
- **Báo cáo bằng chứng (Evidence Generation):** Tự động đóng gói hồ sơ bằng chứng đạt chuẩn kiểm định ASIL-D.

## 📁 Cấu trúc dự án

```text
asil-d-orchestrator/
├── src/
│   ├── parser.py           # Phân tích Java AST (Tree-sitter)
│   ├── cfg_builder.py      # Xây dựng Đồ thị luồng điều khiển (CFG)
│   ├── flow_analysis.py    # Phân tích luồng dữ liệu (DFG) & Call Graph
│   ├── semantics.py        # Gán nhãn ngữ nghĩa ASIL & Safety
│   ├── bootstrap.py        # Quản lý chế độ khởi tạo (Bootstrap) & Rủi ro
│   ├── agents.py           # Các AI Agent (Safety, Unit, MC/DC, Mutation)
│   ├── memory.py           # Bộ nhớ chia sẻ giữa các Agent (Shared Knowledge Bus)
│   ├── evidence.py         # Công cụ tạo bằng chứng & Ký số
│   └── orchestrator.py     # Luồng điều khiển trung tâm
├── tests/                  # Mã nguồn Java mẫu để kiểm thử
├── .graph/                 # Lưu trữ các phiên bản đồ thị & Cache
└── evidence/               # Kết quả bằng chứng sau mỗi lần chạy
```

## 🛠 Cài đặt

Yêu cầu Python 3.10+ và các thư viện hỗ trợ phân tích mã:

```bash
pip install tree-sitter tree-sitter-java
```

## 🔄 Quy trình làm việc (Workflow)

Hệ thống vận hành theo 5 giai đoạn chính:

### 1. Chế độ thực thi (Mode Detection)
Hệ thống tự động phát hiện:
- **BOOTSTRAP:** Chạy lần đầu, phân tích toàn bộ dự án.
- **INCREMENTAL:** Chỉ phân tích các tệp có thay đổi (dựa trên git diff/hash).

### 2. Phân tích đồ thị & Ngữ nghĩa
- **Parsing:** Quét mã Java và trích xuất cấu trúc logic.
- **Semantic Mapping:** Xác định `asilLevel` và `safetyCritical` từ Annotation và Comment.
- **Flow Construction:** Xây dựng CFG (nhận diện nhánh) và DFG (truy vết biến).

### 3. Đánh giá & Ưu tiên (Prioritization)
- Tính toán `Risk Score` dựa trên: ASIL Level, độ phức tạp (Cyclomatic), và tầm quan trọng của hàm.
- Sắp xếp hàng đợi thực thi: Các hàm ASIL-D luôn được ưu tiên xử lý trước.

### 4. Điều phối AI Agent
Các Agent thực hiện nhiệm vụ tuần tự và chia sẻ thông tin qua **Agent Memory Layer**:
1. **Safety Agent:** Kiểm tra checklist an toàn (Null check, Resource leak...).
2. **Unit Test Agent:** Tạo khung kiểm thử JUnit5.
3. **MC/DC Agent:** Phân tích cặp điều kiện độc lập cho các nhánh logic phức tạp.
4. **Mutation Agent:** Phân tích các mutant sống sót để tạo test case tiêu diệt chúng.

### 5. Tổng hợp bằng chứng (Evidence)
- Kiểm tra các cổng chất lượng (Quality Gates).
- Tạo gói bằng chứng `EP-{timestamp}` bao gồm:
    - `verification_summary.json`: Tổng hợp kết quả.
    - `run_manifest.json`: Chứa mã băm (hash) xác thực và chữ ký số.

## 💻 Cách chạy

Để bắt đầu phân tích một thư mục dự án Java:

```bash
python3 asil-d-orchestrator/src/orchestrator.py <đường_dẫn_dự_án_java>
```

**Ví dụ:**
```bash
python3 asil-d-orchestrator/src/orchestrator.py asil-d-orchestrator/tests
```

## 📊 Tiêu chuẩn chất lượng (Quality Gates)

| Chỉ số | Ngưỡng yêu cầu (ASIL-D) |
| :--- | :--- |
| Branch Coverage | >= 95% |
| MC/DC Coverage | 100% |
| Mutation Score | >= 90% |
| Safety FAIL Items | 0 |
| Test Quality Score | >= 70/100 |

---
*Tài liệu này được tạo tự động bởi ASIL-D Orchestrator v3.0.*
