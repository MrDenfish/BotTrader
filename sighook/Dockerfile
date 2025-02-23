FROM python:3.10.12

LABEL authors="Manny"

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file and install dependencies
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# Copy the 'Shared_Utils' directory from the root project folder
COPY ../Shared_Utils /app/Utils

# Copy the remaining application code into the container
COPY . /app

# Environment variables
ENV RUNNING_IN_DOCKER=true
ENV PYTHONPATH=/app
# Command to run the application
CMD ["python", "sighook/sender.py"]
