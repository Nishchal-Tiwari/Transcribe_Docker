FROM  public.ecr.aws/lambda/python:3.12

# Install essential packages
RUN dnf -y install git wget tar xz

# Download and install a static build of ffmpeg
RUN wget https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz && \
    tar xvf ffmpeg-release-amd64-static.tar.xz && \
    mv ffmpeg-*-amd64-static/ffmpeg /usr/bin/ffmpeg && \
    rm -Rf ffmpeg*

# Install Whisper dependencies
RUN pip install --no-cache-dir setuptools-rust

# Download and install openai-whisper from GitHub tarball
RUN pip install openai-whisper
# RUN whisper --model_dir /usr/local --model medium audio >> /dev/null 2>&1; exit 0
# Copy the Lambda function code into the container
COPY lambda_function.py ${LAMBDA_TASK_ROOT}

# Set the CMD to your handler
CMD ["lambda_function.handler"]
