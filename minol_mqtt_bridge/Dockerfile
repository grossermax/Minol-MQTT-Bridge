# Minimal Python base. Authentication is browser-less (pure requests), so the
# image ships only small pure-Python dependencies. Using the Alpine (musl) base
# instead of Debian-slim keeps the image (and therefore Home Assistant backups)
# as small as possible.
#
# "3.14-alpine" tracks the latest Python on the current Alpine release.
FROM python:3.14-alpine

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

# Set working directory
WORKDIR /app

# Install requirements
COPY requirements.txt requirements.txt

# Install the (pure-Python) dependencies and clean up caches. All runtime
# dependencies ship musl/pure-Python wheels, so no compiler toolchain is needed.
RUN pip install --no-cache-dir --root-user-action=ignore --upgrade pip &&\
    pip install --no-cache-dir --root-user-action=ignore --requirement requirements.txt &&\
    rm -rf /root/.cache /tmp/*

# Copy necessary files
COPY run.sh /app/
COPY minol_connector.py /app/
COPY main.py /app/

# Make the run script executable
RUN chmod a+x /app/run.sh

# Start the application
CMD [ "/app/run.sh" ]
