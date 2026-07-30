FROM python:3.10-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /workspace

# The runtime supplies a numeric non-root UID/GID, a read-only root filesystem,
# dropped capabilities, resource limits, and the role-specific workspace mount.
ENTRYPOINT []
CMD ["python", "--version"]
