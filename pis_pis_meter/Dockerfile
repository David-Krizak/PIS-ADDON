ARG BUILD_FROM=ghcr.io/hassio-addons/base-python/amd64:16.1.4
FROM ${BUILD_FROM}

WORKDIR /usr/src/app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

RUN chmod -R a+rX /usr/src/app

# >>> add this line <<<
ENV PYTHONPATH=/usr/src/app

COPY run.sh /run.sh
RUN chmod a+x /run.sh

CMD [ "/run.sh" ]
