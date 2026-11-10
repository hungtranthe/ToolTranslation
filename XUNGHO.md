# Nhân vật và xưng hô

Quy trình chạy offline, không tự sửa `thuatngu.txt` hoặc tự dịch lại truyện.
Trang H cần `openpyxl` trong **cùng môi trường Python chạy `serve_ui.py`**.
Nếu trang báo thiếu thư viện, chạy lệnh cài đặt hiển thị trong cảnh báo (dùng
đúng đường dẫn Python của ứng dụng), rồi khởi động lại ứng dụng. Cũng có thể
cài từ danh sách phụ thuộc bằng `& "đường-dẫn-python.exe" -m pip install -r requirements-xungho.txt`.
Trong PowerShell, chạy từ thư mục ứng dụng:

```powershell
$p = 'D:\8man\Ciweimao\Chính trị và pháp luật hệ Hikigaya Hachiman'
$report = Join-Path $p 'Chính trị và pháp luật hệ Hikigaya Hachiman_分析报告.md'
python nhanvat_tool.py entities --reader $report --moegirl-dir .\moegirl --works (Join-Path $p 'moegirl_works_approved.txt') --glossary (Join-Path $p 'thuatngu.txt') --aliases (Join-Path $p 'aliases.txt') --overrides (Join-Path $p 'overrides.tsv') --out $p
python nhanvat_tool.py glossary --glossary (Join-Path $p 'thuatngu.txt') --out $p
python nhanvat_tool.py xungho --glossary (Join-Path $p 'thuatngu.txt') --rel-types (Join-Path $p 'rel_types.txt') --out $p
```

1. Duyệt `entities.xlsx`; bí danh AI Reader chỉ là bằng chứng, không tự gộp.
   Chạy lại `entities` sau khi điền quyết định. Nhân vật có cách gọi khớp
   chính xác và không xung đột trong `thuatngu.txt` được nhận diện tự động,
   kể cả dòng tiếng Việt/Latin đặt trước tiếng Trung. Không cần thêm nhãn
   `# nhân vật` và không sửa file thuật ngữ. Cách gọi mơ hồ hoặc có nhiều đích
   cần duyệt thủ công. Duyệt `thuatngu.candidates.txt` để bổ sung nhân vật mới.
2. Chạy `xungho`, điền các ô vàng trong `xungho.xlsx`, lưu và đóng Excel, rồi
   chạy lại `xungho`. Các dòng đã điền được xuất ra `xungho.txt` và
   `xungho.rules.json`. Ô giữ nguyên theo người nói, người nghe và chương bắt
   đầu; dòng không còn khớp được chuyển sang sheet **Mồ côi**. Quan hệ và
   trích dẫn chỉ là bằng chứng; không tự suy ra chiều hoặc cách xưng hô.
3. Với engine `dichTrungsangViet.py`, file `xungho.rules.json` cạnh văn bản
   nguồn được nạp khi bắt đầu dịch. Sửa glossary thì phải chạy lại `xungho`
   trước khi dịch. Các quy tắc chỉ là hướng dẫn cho model, không bảo đảm model
   tuân thủ; hãy dịch thử trước khi quyết định dịch lại toàn bộ.

Báo cáo AI Reader và file nguồn phải khớp tiêu đề chương **duy nhất**; chương
không khớp không được áp dụng quy tắc và số lượng được ghi trong nhật ký.
Chỉ gửi quy tắc khi cả hai tên nhân vật xuất hiện trong đoạn; đoạn không nêu
tên hoặc tên mơ hồ có thể không nhận được gợi ý. Không tự xác định người nói.
Chế độ DeepSeek chưa sử dụng file quy tắc này.

Trên trang **H. Quản lý xưng hô**, bấm **Chạy entities** để phân tích lại báo
cáo AI Reader. Ứng dụng đề xuất báo cáo đã dùng; nếu chưa biết báo cáo, hãy
chọn file `.md`, rồi xác nhận và chờ phân tích hoàn tất. Sau đó bấm
**Phân tích & duyệt nhân vật** để tìm nhân vật, xem các cách gọi và bằng chứng, chọn từng cách gọi rồi
xác nhận tên dịch và giới tính để ghi vào glossary (có sao lưu). Các bí danh
AI Reader chỉ là bằng chứng. Trong **Tên mơ hồ**, chọn ứng viên rồi lưu; chạy
lại phân tích để áp dụng quyết định. Sau khi duyệt nhân vật, đóng khung và bấm
**Tạo lại bảng** để bắt đầu duyệt xưng hô.

Trang H đọc bảng của dự án, có thể tạo lại bảng và sửa,
chốt rồi lưu các dòng vào Excel và file quy tắc dịch. Chọn một dòng trong bảng,
rồi chọn **Điền nhanh xưng hô đã lưu** ở khung bằng chứng để điền cặp
**tự xưng – gọi người nghe** từ các dòng đã lưu của dự án. Cặp trùng được gộp;
các dòng mới sửa chưa lưu không xuất hiện trong danh sách. Việc chọn không đổi
ghi chú; nếu dòng đã có cách xưng hô khác, cần xác nhận trước khi thay. Bấm
**Lưu các dòng đã chốt** để ghi thay đổi vào dự án.
Nút **Áp dụng cho các giai đoạn trống** dùng cách xưng hô của dòng đang chọn
cho các dòng hoàn toàn trống của cùng cặp theo chiều người nói → người nghe,
kể cả ngoài trang đang hiển thị. Dòng có nội dung, gợi ý hoặc đã chốt và chiều
ngược lại không bị thay đổi. Nút không tạo giai đoạn mới, nên không bao phủ
các chương trước giai đoạn đầu tiên nếu bảng chưa có dòng cho chúng. Cần bấm
**Lưu các dòng đã chốt** để ghi thay đổi.
Nút **Lưu tất cả từ đây về sau** ghi ngay cách xưng hô của dòng đang chọn vào
dòng đó và mọi giai đoạn sau của cùng chiều, kể cả dòng đã có cách xưng hô,
sau khi xác nhận. Nút giữ nguyên ghi chú đã lưu, không đổi chiều ngược lại và
không tạo giai đoạn mới. Các chỉnh sửa khác chưa lưu vẫn ở trên giao diện và
cần lưu riêng; nếu dữ liệu dự án đã thay đổi bên ngoài, thao tác sẽ báo lỗi.
**Tổng hợp quan hệ & quy
tắc** hiển thị nhãn gốc, bản phân nhóm và giới tính của cặp A → B theo đúng
chiều ghi trong báo cáo; bấm vào số cặp để xem tên, chương báo cáo và trích
dẫn. Chiều này không xác định ai đang nói, và báo cáo có thể ghi sai quan hệ
hoặc giới tính: cần kiểm tra bằng chứng trước khi quyết định. Chỉ các cặp có
thể xác định được cả hai nhân vật mới được thống kê. Cột báo cáo đếm chương-cặp
theo nhãn gốc; cột bảng đếm dòng xưng hô A → B đã đủ điều kiện duyệt, có cùng
nhãn trong giai đoạn và cùng nhóm quan hệ trong bảng. Không tự tạo chiều ngược
trong thống kê; dòng B → A chỉ được gợi ý nếu báo cáo có ghi chiều đó. Một dòng
có thể xuất hiện dưới nhiều nhãn gốc cùng nhóm. Các ô quy tắc chỉ tồn tại trong phiên hiện tại:
nhập cách xưng hô rồi bấm **Gợi ý**, rà soát các dòng, bấm **Chốt gợi ý** và
xác nhận, sau đó **Lưu các dòng đã chốt**. Các dòng đã chốt trước không bị
ghi đè. Giới tính chưa rõ được để riêng, không suy đoán vai trên/dưới.
Với anh chị em, bảng hiển thị vai theo thứ tự A → B, ví dụ **Anh trai – Em
gái**. Với nhãn `兄妹` hoặc `姐弟`, nếu giới tính hai nhân vật xác định và
phù hợp, vai được gợi ý theo nhãn và giới tính, không phải kết luận đã duyệt.
Các nhãn khác, giới tính thiếu hoặc không phù hợp để **chờ duyệt**. Mở danh
sách cặp, chọn vai A → B và bấm **Lưu vai A → B** để duyệt hoặc sửa; chọn
**Theo báo cáo / chưa rõ** để bỏ quyết định thủ công. Vai duyệt lưu riêng trong
`xungho.roles.json` theo báo cáo, cặp nhân vật và nhãn gốc, tồn tại sau khi tải
lại; không tự trở thành quy tắc dịch. Nhóm chưa rõ vai không cho gợi ý hàng
loạt; hãy duyệt từng cặp trước. Quyết định vai không thay đổi các dòng xưng hô
đã chốt. Các quyết định không còn khớp báo cáo sẽ được cảnh báo và không áp dụng.
Trường “Ghi chú / gọi tên” tương ứng cột ghi chú trong Excel. Nếu sửa file
ngoài ứng dụng, hãy tải lại trước khi lưu. Ngưỡng lọc số chương của cặp và
quản lý persona riêng chưa được hỗ trợ; không coi các quy ước còn mở trong
`structured.md` là đã được phê duyệt.
