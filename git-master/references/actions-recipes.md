# Actions 配方：CI 门禁 / 自动发布 / 自建部署 / 失败通知

git-master M6 模式的落地模板。逐段复制到目标项目的 `.github/workflows/<名>.yml`，提交推送即生效。所有密钥走仓库 Settings → Secrets and variables → Actions。

## ci.yml — CI 门禁（push/PR 自动 lint + 测试，含缓存与矩阵）

```yaml
name: CI
on:
  push:
    branches: [master]
  pull_request:

jobs:
  test:
    strategy:
      fail-fast: false
      matrix:
        os: [ubuntu-latest]
        python: ['3.10', '3.12']
    runs-on: ${{ matrix.os }}
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: ${{ matrix.python }}
          cache: pip                    # 依赖缓存默认接线（pip 换 npm 即 Node 项目）
      - run: pip install -e . ruff pytest
      - run: ruff check .
      - run: pytest -q
```

- 单人项目默认只留 `ubuntu-latest` 一档；发跨平台二进制才开全矩阵（加 `windows-latest`、`macos-latest`）。
- 缓存何时有感知：CI 总时长超过 2 分钟且依赖安装占大头（重依赖、矩阵重复安装）时收益明显；轻项目加了无感，保留无害。
- Node 项目：`actions/setup-python@v5` 换 `actions/setup-node@v4`，`cache: pip` 换 `cache: npm`。

## release.yml — tag 自动发布（构建 → Release → PyPI → 自建站点 → 失败通知）

打 `v<版本>` tag 推送后全自动执行；不部署站点就删掉 deploy job，不发 PyPI 就删掉 pypi 步骤。

```yaml
name: Release
on:
  push:
    tags: ['v*']

permissions:
  contents: write      # 建 GitHub Release
  id-token: write      # PyPI Trusted Publishing（免 token）

jobs:
  build:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: '3.12'
          cache: pip
      - run: pip install build && python -m build
      - uses: actions/upload-artifact@v4
        with:
          name: dist
          path: dist/

  release:
    needs: build
    runs-on: ubuntu-latest
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: dist
          path: dist/
      - uses: softprops/action-gh-release@v2
        with:
          files: dist/*
          generate_release_notes: true
      - uses: pypa/gh-action-pypi-publish@release/v1

  deploy:
    needs: release
    runs-on: ubuntu-latest
    environment: production          # 可在 Settings → Environments 配人工审批门
    steps:
      - uses: actions/download-artifact@v4
        with:
          name: dist
          path: dist/
      - name: rsync 到自建站点（A 路线：SSH 推送）
        run: |
          mkdir -p ~/.ssh
          echo "${{ secrets.DEPLOY_KEY }}" > ~/.ssh/id_ed25519
          chmod 600 ~/.ssh/id_ed25519
          ssh-keyscan -H ${{ secrets.DEPLOY_HOST }} >> ~/.ssh/known_hosts
          rsync -avz --delete -e "ssh -i ~/.ssh/id_ed25519" \
            dist/ ${{ secrets.DEPLOY_USER }}@${{ secrets.DEPLOY_HOST }}:${{ secrets.DEPLOY_PATH }}
      - name: 重启服务（纯静态站点删掉本步）
        run: |
          ssh -i ~/.ssh/id_ed25519 \
            ${{ secrets.DEPLOY_USER }}@${{ secrets.DEPLOY_HOST }} 'sudo systemctl reload nginx'

  notify:
    needs: [build, release, deploy]
    if: failure()
    runs-on: ubuntu-latest
    steps:
      - name: 推 IM webhook（企业微信/钉钉格式；Slack 把 body 换 {"text":"..."}）
        run: |
          curl -s -X POST -H 'Content-Type: application/json' \
            -d '{"msgtype":"text","text":{"content":"发布失败：${{ github.repository }} ${{ github.ref_name }}"}}' \
            "${{ secrets.NOTIFY_WEBHOOK }}"
```

## Secrets 配置（一次性）

| Secret | 值 | 用途 |
|--------|-----|------|
| `NOTIFY_WEBHOOK` | 企业微信/钉钉群机器人 webhook 地址 | 失败通知 |
| `DEPLOY_KEY` | 专用部署私钥（见下） | SSH 免密推送 |
| `DEPLOY_HOST` | 服务器地址 | rsync 目标 |
| `DEPLOY_USER` | 服务器部署账号 | rsync 目标 |
| `DEPLOY_PATH` | 站点根目录（如 `/var/www/site`） | rsync 目标 |

部署密钥生成与最小权限（服务器需公网可达 SSH）：

```bash
ssh-keygen -t ed25519 -f ~/.ssh/deploy_key -N '' -C 'github-actions-deploy'
# 公钥 ~/.ssh/deploy_key.pub 追加进服务器 ~/.ssh/authorized_keys；
# 部署账号只授予站点目录写权限，禁用交互 shell（force command 或 nologin + 窄 sudo）。
```

PyPI Trusted Publishing（一次性）：pypi.org 项目 → Publishing → 绑定 GitHub 仓库与 workflow 文件名 `release.yml`，之后发版免 token。项目首次发布可先手动 `gh release create` 跑通，再接自动链路。

## 密钥纪律

1. 全部凭证走 Secrets，代码与日志不出现明文；打印 `secrets` 值视为泄露，立即轮换。
2. 第三方 Action 只 pin 版本 tag（`@v4`），不 pin `@master`；不认识的 Action 先看源码再引入。
3. 部署密钥专用，不与个人登录密钥混用；泄露立即删服务器公钥并换 Secrets。
4. 服务器在 NAT 后无法公网 SSH 时，A 路线不可用：改 self-hosted runner（服务器出站装 runner）或服务器定时拉 Release 产物。
