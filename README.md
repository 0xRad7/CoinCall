# CoinCall 平台根仓

本仓库是整个 CoinCall 平台的**版本钉子与分发清单**：`.gitignore` 采用白名单法，只纳管 `coincall-*/` 目录，其余（脚本、报告、本地配置、数据）一概不入仓。

- `coincall-core / gateway / console / sdk / contracts / bot-chain-api / examples` 均为**独立 git 仓库**，在根仓中以 gitlink（裸子模块指针，mode 160000）记录当前 HEAD——只钉版本，内容不入根仓。
- `coincall-docs` 非 git 仓库，以普通文件整体纳管。
- 各子仓独立演进、各自提交，并各自维护 `.gitignore` 与秘密防护；根仓层面额外全量排除 `.env`（保留 `.env.example`）、keystore、DuckDB 数据、日志与各类缓存。
- 升级子仓版本：先在子仓内提交，再回根仓 `git add <子目录>` 更新 gitlink 指针后提交。
