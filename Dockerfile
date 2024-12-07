FROM python:3-alpine

WORKDIR /usr/src/wgroutemgr

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY ./wgroutemgr.py .

CMD [ "python", "./wgroutemgr.py" ]
