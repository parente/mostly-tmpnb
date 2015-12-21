FROM jupyter/minimal-notebook:latest

USER jovyan

COPY auth/by_volume.py \
    /opt/conda/lib/python3.4/site-packages/notebook/auth/
COPY templates/login_register.html \
    /opt/conda/lib/python3.4/site-packages/notebook/templates

#RUN pip install git+https://github.com/danielballan/nbexamples.git#egg=nbexamples

USER root
