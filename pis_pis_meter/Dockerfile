ARG BUILD_FROM
FROM ${BUILD_FROM}

WORKDIR /usr/src/app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

# Copy the full add-on source so the package modules are available when running
COPY . .

# Ensure the startup script is executable
RUN chmod a+x /run.sh

EXPOSE 8080

CMD ["/run.sh"]
