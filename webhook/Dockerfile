FROM python:3.10.12

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file and install dependencies
COPY requirements.txt .

RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code into the container
COPY . /app

# Install dependencies, copy files, etc.
ENV RUNNING_IN_DOCKER=true
ENV PORT=5000
# Command to run the application
CMD ["python", "listener.py"]