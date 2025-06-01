# Use an official Python runtime as a parent image
FROM python:3.9-slim

# Set the working directory in the container
WORKDIR /app

# Copy the dependencies file to the working directory
COPY requirements.txt .

# Install any needed packages specified in requirements.txt
RUN pip install --no-cache-dir -r requirements.txt

# Copy the content of the local src directory to the working directory
COPY . .

# Make port 2494 available to the world outside this container
EXPOSE 2494

# Define environment variable for the port (optional, for flexibility)
ENV PORT 2494
ENV HOST 0.0.0.0

# Run app.py when the container launches
# Use 0.0.0.0 to allow access from outside the container
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "2494"] 