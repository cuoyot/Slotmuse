FROM python:3.12-slim
WORKDIR /app
COPY server.py school_app.py ./
COPY static ./static
RUN mkdir /app/data && chown -R 10001:10001 /app
USER 10001:10001
ENV HOST=0.0.0.0 PORT=8000 DATABASE_PATH=/app/data/academia.db
EXPOSE 8000
CMD ["python", "school_app.py"]
