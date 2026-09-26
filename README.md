# 企业排污许可与超标处置

汇总监测和工况，判断排放超标并跟踪复测、整改、执法与复查。

## 模块结构

- `app.py`：参数解析、依赖组装和HTTP服务启动。
- `src/domain.py`：数据结构、错误、状态和基础校验。
- `src/rules.py`：状态机、角色矩阵、优先级、期限、关闭不变量和复核修订规则。
- `src/repository.py`：SQLite建表、事务、版本控制、修订存储和审计链。
- `src/service.py`：权限检查、用例编排、并发控制和审计。
- `src/http_api.py`：JSON路由和统一错误响应（含修订请求入口）。
- `src/audit.py`：UTC时间和SHA-256审计事件。
- `static/index.html`：最小演示页。
- `tests/`：完整流程、规则和失败测试。

## 初始化与启动

```bash
python3 app.py --db ./data.db --port 8313
```

默认端口为`8313`，首次启动自动建库。使用`X-Actor`和`X-Role`请求头传递身份。

## 主要接口

- `GET /health`
- `GET /api/items`
- `POST /api/items`
- `GET /api/items/{id}`
- `POST /api/items/{id}/records`
- `POST /api/items/{id}/transition`，必须提交`expected_version`
- `GET /api/items/{id}/revisions`
- `POST /api/items/{id}/revisions`，合规员登记修订：`original_value`、`new_value`、`material_ref`、`expected_version`
- `POST /api/items/{id}/revisions/{rid}/apply`，复核生效（合规员/主任）
- `POST /api/items/{id}/revisions/{rid}/reject`，复核驳回（合规员/主任）
- `GET /api/audit`

允许角色：operator, compliance_officer, director, viewer。按浓度与许可限值计算超标倍数，异常读数先进入评估；关闭前必须没有未完成整改项。

## 复核修订流程

监测记录填错时由合规员登记修订（原值、新值、材料编号、事件版本），修订单进入`pending`；同一事件同一时间只允许一条待复核修订。复核生效后系统按新值重算优先级与处置期限，若新结论不再需要处置，未完成整改记录一并按新结果重判关闭；旧结论随修订单留档并标记`superseded`失效。存在待复核修订时关闭事件返回409提示先处理。列表与详情均返回`revision_batch`（已生效修订批次）、`pending_revisions`、`can_close`和`close_blockers`（阻塞原因）。

## 测试

```bash
python3 -m unittest discover -s tests -v
```
