# 日常银行事务助手 v0.1.0

本项目是使用虚构数据的银行事务演示，不连接真实银行、支付渠道或商户。查询、订阅取消、账单更正、模拟工资、还款和转账仅作用于隔离演示会话。

## 本地运行

需要 Python 3.12、[uv](https://docs.astral.sh/uv/) 和 PostgreSQL 17。

1. 安装依赖：

   ```sh
   uv sync --extra test
   ```

2. 创建本地演示数据库，并按顺序应用迁移：

   ```sh
   createdb fintech_demo
   export MIGRATION_ADMIN_URL='postgresql://localhost:5432/fintech_demo'
   for migration in migrations/*.sql; do
     psql "$MIGRATION_ADMIN_URL" -v ON_ERROR_STOP=1 -f "$migration"
   done
   ```

3. 配置本机演示环境并启动：

   ```sh
   export DATABASE_URL='postgresql://demo_runtime@localhost:5432/fintech_demo'
   export DEMO_ACCESS_CODE='local-demo-only'
   export COOKIE_SECURE=false
   cp .env.example .env
   # 在本机编辑 .env，填入本机数据库、演示访问码和模型密钥。
   uv run --env-file .env uvicorn app.main:app --host 127.0.0.1 --port 8000
   ```

   在线任务草案需要服务端 `DEEPSEEK_API_KEY`。密钥仅放在受保护的本机或部署环境变量中，不要提交到源码、网页或演示材料。打开 <http://127.0.0.1:8000> 后使用本机演示访问码。红色流程使用固定模拟验证码和同参数确认，不是真实多因子认证。

## 部署

部署运行环境需要提供 `DATABASE_URL`、随机 `DEMO_ACCESS_CODE`、`COOKIE_SECURE=true` 和 `DEEPSEEK_API_KEY`。部署前先在目标 PostgreSQL 实例按顺序应用 `migrations/001` 至 `migrations/011`，并检查数据库角色与 RLS 权限；然后部署 FastAPI 应用并核对 `/health/live`、`/health/ready`。不要将访问码、连接串或模型密钥写入仓库。仅使用虚构数据，不连接真实金融服务。

## 验证

```sh
uv run --extra test pytest -q
```

测试覆盖计划结构、会话隔离、逐步授权、熔断、任务状态、回执与账本行为。若本机未配置 PostgreSQL 专项测试环境，相应集成用例会跳过；SQLite 测试不替代真实 PostgreSQL、RLS 或部署验证。

## 能力边界

DeepSeek 只生成计划草案；服务端校验意图、参数、步骤依赖与权限后才执行。任务可暂停、恢复、取消未执行步骤或转人工处理；已成功的资金操作不自动回滚。风险评分仅是虚构小样本实验提示，不决定权限或执行。虚拟卡申请、密码修改、卡片挂失／解挂、理财收益查询／申购、真实短信／人脸认证和生成代码执行不在本版本范围内。
