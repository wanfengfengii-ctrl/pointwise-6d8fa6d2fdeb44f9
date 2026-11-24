# 杉湾材料试验厂返修周转工作台

Python 3.11 标准库与 SQLite。试片路线留在各自的站库，中心以实收原件核算返修周转。

## 一个工作日

1. 日班工艺员调用 `routes.intake.register_route(revisions, draft, parent_revision=None)`，登记一份 `RouteDraft(station, entries, steps)`。`entries` 将公开入口映射到私有工位，`steps` 将工位映射到一组 `RouteAction(kind, probability, minutes, target=None)`。动作类型为 `step`、`release`、`handoff`、`hold`。前两种带目标的动作是 `step` 和 `handoff`，分别指向本地工位及对站入口。
2. 勘误用同一入口提交完整路线及同站父版号。返回新修订号，`RevisionStore.get(station, revision_id)` 仍可取父版。空入口、空工位、无效动作、非法耗时、坏目标、概率和或父版错误均以 `RegistrationRejected` 拒绝。概率接受 `Fraction`、整数或精确数字字符串。
3. 值班员调用 `station.issue.freeze_station(revisions, evidence, station, revision_id)` 取得固定原件的字节。缺修订以 `FreezeRejected` 拒绝。已发行原文可由 `EvidenceStore.get(piece_id)` 领取。一个站号对应一份权威工艺库。
4. 中心调用 `exchange.receive.receive_station_piece(receipts, raw)`，核对格式、摘要、身份和证据质量，返回 `ReceivedPiece`。重交相同字节复用实收号，损坏或同号异文报 `ReceiptRejected`。摘要用于核对内容完整性。
5. 用 `exchange.pair.pair_received(pairs, first_receipt_id, second_receipt_id)` 固定两份实收件及端口接续。缺件、同站双件或对站无对应口均报 `PairingRejected`。来源站及对站身份参与端口匹配，两站可以复用口名。
6. 关站源后，夜班调用 `review.request.report_turnaround(pairs, reports, ReportRequest(pair_id, start_station, start_entry, deadline))`。截止为非负整数分钟，起点是所选站件之一的公开入口。空值、负值及无效配对或入口按 `RequestRejected` 处理。
7. 签发提交后返回 `FinalReport`，含报告号、两列分数、双件出处、原件和成功回执。SQLite 写入或提交失败报 `IssuanceFailed`，本次报告和回执一起回滚。
8. 次班调用 `reports.history.read_report(reports, report_id)` 或 `reports.export.export_report(reports, report_id)`。导出是规范 JSON 字节，分数用精确字符串，原件用 Base64。无此定稿报 `ReportNotFound`。读回只查询归档表。

仓储构造函数接收调用方持有的 `sqlite3.Connection`。站库分别初始化 `RevisionStore` 和 `EvidenceStore`，中心库依次初始化 `ReceiptStore`、`PairStore` 和 `ReportStore`。反复 `initialize()` 保留所有已提交数据。业务写入自行管理事务，调用前应结束调用方的写事务。各标识符以所属库为范围，站件身份采用站号和修订号的规范摘要。

## 计算口径

每个工位的非负动作概率精确合计为一，步骤耗时为零或一分钟。站件按公开入口及首次去向分别编码各分钟的概率质量，并记录没有本站首出事件的质量。正文中的非负有理表达式含多项式、和、积及可收敛的重复项。计算时累加动作耗时，接续到对站入口本身耗时为零。同一分钟内允许反复交班。中心累计截止内的首次放行，另算最终首次放行。永久留置及无出口回流均占全批分母，放行质量为零。零概率是合法结果。

## 运行

```sh
python3 -B check_project.py
python3 -B -m unittest discover -s tests -t .
python3 -B demo.py
```

标准回归入口由运行平台提供。演示装载 `samples/day.json`，发行旧版和勘误版双件，关源后签发，再用新连接按原号领取。旧版截止零和一分钟为 `2/7` 和 `22/49`，最终为 `2/3`。新版把甲站重试改为即时，截止零为 `2/3`。演示在临时目录运行。回归另核对固定修订、坏件、错口、恰逢截止、两类封闭循环、签发回滚及重开后原文导出。
