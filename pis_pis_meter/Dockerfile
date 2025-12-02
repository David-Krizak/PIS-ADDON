ARG BUILD_FROM=ghcr.io/hassio-addons/base-python/amd64:16.1.4
FROM ${BUILD_FROM}

WORKDIR /usr/src/app

# Copy your python package
COPY pis_pis_meter/ ./pis_pis_meter/
COPY requirements.txt .

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Fix permissions for Home Assistant
RUN chmod -R a+rX /usr/src/app

# Start the add-on
COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
