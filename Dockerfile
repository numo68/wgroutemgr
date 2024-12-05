FROM python:3-alpine

WORKDIR /usr/src/wgroutemgr

COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt

COPY ./wgroutemgr.py .

CMD [ "python", "./wgroutemgr.py" ]

# docker run -it --rm --name wgroutemgr --privileged -v "$PWD":/usr/src/wgroutemgr -w /usr/src/wgroutemgr wgroutemgr python wgroutemgr.py

# docker run -it --rm --privileged --name wgroutemgr -v "$PWD":/usr/src/wgroutemgr -v /var/run/docker.sock:/var/run/docker.sock  -w /usr/src/wgroutemgr wgroutemgr python wgroutemgr.py