# Sequoia-X Web（只读选股结果查询）运行镜像。
#
# 设计取舍：**只烘依赖，不烘业务代码**。
# 源码在运行时以只读方式挂到 /app，保留服务器上 `git pull` 直接更新代码的既有流程，
# 改前端/后端代码都不必重建镜像；只有 pyproject.toml 的依赖清单变了才需要 rebuild。
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

# 只 COPY pyproject.toml + 一个同名空包：让 pip 把 9 个依赖装齐，
# 但不把业务代码烘进镜像。依赖清单因此与 pyproject.toml 单一来源同步，不会漂移。
COPY pyproject.toml ./
RUN mkdir -p sequoia_x \
    && touch sequoia_x/__init__.py \
    && pip install --no-cache-dir . \
    && pip uninstall -y sequoia-x \
    && cd / \
    && rm -rf /build

WORKDIR /app
# 与宿主 ubuntu 用户对齐（实测 uid=1001 gid=1001，不是常见的 1000）：
# .env 是 0600、data/ 是 0775，都归 1001，uid 对不上会 PermissionError
USER 1001:1001

# 容器内监听 0.0.0.0 只在自己的 network namespace 里，不 publish 端口就出不了 docker 网络。
# 宿主 iptables 链尾 REJECT 让容器够不到宿主端口，所以这个服务必须容器化才能被 nginx 反代。
EXPOSE 8000
CMD ["python", "serve.py", "--host", "0.0.0.0", "--port", "8000"]
