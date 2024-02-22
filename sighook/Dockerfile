# Use an official Ubuntu base image
# docker-compose.network.yaml (blueprint for building images), Image(template for running containers),
# Container (where the program resides and runs)
# Dockerfile sighook`
FROM python:3.10.12

LABEL authors="Manny"

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code into the container
COPY . /app

# Install dependencies, copy files, etc.
ENV RUNNING_IN_DOCKER=true
# Command to run the application
CMD ["python", "sender.py"]