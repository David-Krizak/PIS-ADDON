ARG BUILD_FROM=ghcr.io/hassio-addons/base-python:14.3.2
FROM ${BUILD_FROM}

WORKDIR /usr/src/app

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY scraper.py app.py ./

COPY run.sh /run.sh
RUN chmod a+x /run.sh

EXPOSE 8080

CMD ["/run.sh"]
