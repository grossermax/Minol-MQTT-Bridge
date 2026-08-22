# Use the official playwright python image which includes browsers
FROM mcr.microsoft.com/playwright/python:v1.62.0-jammy

# Set working directory
WORKDIR /app

# Install requirements
COPY requirements.txt requirements.txt

# Install python dependencies
# using --root-user-action=ignore to supress WARNING: Running pip as the 'root' user can result in broken permissions
RUN apt-get update && apt-get clean &&\
    pip install --upgrade pip &&\
    pip install --no-cache-dir --root-user-action=ignore --upgrade --requirement requirements.txt

# Copy necessary files
COPY run.sh /app/
COPY minol_connector.py /app/
COPY main.py /app/

# Make the run script executable
RUN chmod a+x /app/run.sh

# Start the application
CMD [ "/app/run.sh" ]
