ARG BUILD_FROM=ghcr.io/hassio-addons/base-python/amd64:16.1.4
FROM ${BUILD_FROM}

WORKDIR /usr/src/app

# Install dependencies first for better layer caching
COPY requirements.txt ./

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy application source into package directory so python -m pis_pis_meter works
COPY . ./pis_pis_meter

# Fix permissions for Home Assistant
RUN chmod -R a+rX /usr/src/app

# Start the add-on
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
