# Use an official Ubuntu base image
# docker-compose.network.yaml (blueprint for building images), Image(template for running containers),
# Container (where the program resides and runs)
FROM python:3.9

LABEL authors="Manny"

# Set the working directory in the container
WORKDIR /app

# Install Python and pip
RUN apt-get update && \
    apt-get install -y python3-pip python3-dev

# Copy the requirements1.txt file into the container at /app
COPY requirements.txt /app/

# Install any needed packages specified in requirements1.txt
RUN pip3 install --no-cache-dir -r requirements.txt

# Copy the application's code
COPY . /app

# The port that the app intends to runs on NOT ACTUALLY SET HERE
EXPOSE 5001

# Define environment variable
ENV NAME World


# Run app.py when the container launches
CMD ["python3", "./sender.py"]
# CMD ["python3", "./listener.py"]