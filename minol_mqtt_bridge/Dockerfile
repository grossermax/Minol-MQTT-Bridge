# Slim Python base instead of the full Playwright image.
# The official mcr.microsoft.com/playwright/python image ships Chromium,
# Firefox AND WebKit plus all their system libraries (~1.5 GB). This add-on
# only ever launches Chromium, so we start from a small base and install just
# the Chromium browser -> dramatically smaller image and HA backups.
#
# NOTE: We deliberately use a Debian "slim" image and NOT Alpine:
#   * Playwright's bundled Chromium is linked against glibc; Alpine uses musl
#     and is not supported by Playwright (the browser would not start).
#   * `playwright install --with-deps` only works on Debian/Ubuntu (apt).
#   * The dominant size is Chromium (~300 MB), so Alpine's smaller base
#     (~20 MB vs ~50 MB) would barely change the final image size anyway.
# "3.14-slim" tracks the latest Python on the current Debian release.
FROM python:3.14-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright

# Set working directory
WORKDIR /app

# Install requirements
COPY requirements.txt requirements.txt

# Install python dependencies, then ONLY the lightweight Chromium headless
# shell (the add-on always runs headless=True). This avoids the full Chromium
# build (~640 MB), FFmpeg and the headed browser, keeping the image / Home
# Assistant backups as small as possible. Afterwards we drop the big CJK/emoji
# font packages that Playwright pulls in but that a login-form scrape never
# needs, and clean up every cache.
RUN pip install --no-cache-dir --root-user-action=ignore --upgrade pip &&\
    pip install --no-cache-dir --root-user-action=ignore --requirement requirements.txt &&\
    playwright install --with-deps chromium-headless-shell &&\
    apt-get purge -y --auto-remove \
        fonts-unifont fonts-wqy-zenhei fonts-ipafont-gothic fonts-noto-color-emoji &&\
    apt-get clean &&\
    rm -rf /var/lib/apt/lists/* /root/.cache /tmp/*

# Copy necessary files
COPY run.sh /app/
COPY minol_connector.py /app/
COPY main.py /app/

# Make the run script executable
RUN chmod a+x /app/run.sh

# Start the application
CMD [ "/app/run.sh" ]
