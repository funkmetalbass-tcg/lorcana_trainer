# c6i.24xlarge is Intel x86 -> build for linux/amd64
FROM python:3.10-slim

WORKDIR /app

# Install deps first so this layer caches when only code/data changes.
# If you have no requirements.txt, create one (pip freeze / pipreqs) or leave
# it empty if run.py is pure stdlib + multiprocessing.
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the whole repo: run.py, its module, decks, DB.
COPY . .

# No baked args -- you vary parameters between runs. Pass them at `docker run`.
ENTRYPOINT ["python3.10", "./run.py"]
