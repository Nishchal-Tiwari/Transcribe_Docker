import json
import boto3
import os
import whisper
import subprocess
from urllib.parse import unquote_plus

print("s3 init")
# Initialize the S3 client
s3 = boto3.client('s3')

print("Loading Whisper model")
download_root = "/tmp/whisper_model"
os.makedirs(download_root, exist_ok=True)

# Load the Whisper model
model_type = "base"  # Adjust model size as needed
model = whisper.load_model(model_type, download_root=download_root)


def get_video_duration(file_path):
    result = subprocess.run(
        ['ffprobe', '-v', 'error', '-show_entries', 'format=duration',
            '-of', 'default=noprint_wrappers=1:nokey=1', file_path],
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT
    )
    return float(result.stdout)


def transcribe_audio(file_path):
    """
    Transcribes the given audio file and returns the transcription.
    :param file_path: Path to the audio file.
    :return: Transcribed text.
    """
    result = model.transcribe(file_path)
    return result["text"]


def delete_s3_object(bucket_name, object_key):
    """
    Deletes the specified object from the given S3 bucket.
    :param bucket_name: Name of the S3 bucket.
    :param object_key: Key (path) of the object to delete.
    """
    try:
        s3.delete_object(Bucket=bucket_name, Key=object_key)
        print(f"Deleted {object_key} from {bucket_name}")
    except Exception as ex:
        print(f"Error deleting {object_key} from {bucket_name}: {ex}")


def split_file(file_path, bucket_name):
    try:
        # Extract filename and extension
        filename, file_extension = os.path.splitext(
            os.path.basename(file_path))

        # Create a folder name based on the file name and extension
        output_folder = f"{filename}{file_extension}_chunks"
        os.makedirs(f"/tmp/{output_folder}", exist_ok=True)

        # Split the file into 20-minute chunks
        subprocess.run(['ffmpeg', '-i', file_path, '-f', 'segment', '-segment_time',
                       '1200', '-c', 'copy', f'/tmp/{output_folder}/f_%d{file_extension}'])

        # Upload each chunk to S3
        for chunk in os.listdir(f'/tmp/{output_folder}'):
            chunk_path = os.path.join(f'/tmp/{output_folder}', chunk)
            chunk_key = f'{output_folder}/{chunk}'
            s3.upload_file(chunk_path, bucket_name, chunk_key)
            os.remove(chunk_path)  # Clean up the chunk from /tmp after upload
        return True
    except subprocess.CalledProcessError as e:
        print(f"Failed to split file: {e}")


def is_last_chunk(object_key, bucket_name):
    """
    Determines if the current chunk is the last one in the chunk folder.
    """
    folder_path = os.path.dirname(object_key)
    chunk_list = s3.list_objects_v2(
        Bucket=bucket_name, Prefix=folder_path)['Contents']

    # Get the chunk files in the current folder
    chunk_files = [obj['Key']
                   for obj in chunk_list if folder_path in obj['Key']]

    if len(chunk_files) < 2:
        return True
    else:
        return False


def combine_and_clean_chunks(bucket_name, folder_path, result_bucket, base_name):
    """
    Combines all chunk files into one file, saves it to the result bucket, 
    and deletes all chunk files and the chunk folder.
    """
    try:
        # List all chunks in the folder
        chunk_list = s3.list_objects_v2(
            Bucket=bucket_name, Prefix=folder_path)['Contents']

        # Sort chunks by their index
        chunk_list = sorted(chunk_list, key=lambda obj: int(
            os.path.splitext(os.path.basename(obj['Key']))[0].split('_')[-1]))

        combined_transcription = ""

        # Download and combine all chunk files
        for chunk in chunk_list:
            chunk_key = chunk['Key']
            chunk_path = f'/tmp/{os.path.basename(chunk_key)}'
            s3.download_file(bucket_name, chunk_key, chunk_path)
            with open(chunk_path, 'r') as f:
                combined_transcription += f.read() + "\n"
            os.remove(chunk_path)  # Clean up local chunk file

            # Delete chunk file from S3
            delete_s3_object(bucket_name, chunk_key)

        # Save combined transcription to the result bucket
        result_key = f'{folder_path}_combined.txt'
        s3.put_object(Bucket=result_bucket, Key=result_key,
                      Body=combined_transcription)
        print(f"Combined transcription saved to {result_bucket}/{result_key}")

        # Clean up the chunk folder in S3
        s3.delete_object(Bucket=bucket_name, Key=folder_path + '/')
        print(f"Deleted chunk folder {folder_path} from {bucket_name}")

    except Exception as ex:
        print(f"Error combining and cleaning chunks: {ex}")


def handler(event, context):
    try:
        for i in event['Records']:
            s3_event = json.loads(i['body'])
            if 'Event' in s3_event and s3_event['Event'] == 's3:TestEvent':
                print("Test Event!")
            else:
                for j in s3_event['Records']:
                    bucket_name = j['s3']['bucket']['name']
                    object_key = unquote_plus(j['s3']['object']['key'])
                    print(f'Bucket Name: {bucket_name}')
                    print(f'Object Name: {object_key}')

                    # Download the file from S3
                    download_path = f'/tmp/{os.path.basename(object_key)}'
                    s3.download_file(bucket_name, object_key, download_path)

                    duration = get_video_duration(download_path)

                    if duration > 3600:
                        response = split_file(download_path, bucket_name)
                        if response:
                            delete_s3_object(bucket_name, object_key)
                    else:
                        # Transcribe the audio file
                        transcription_text = transcribe_audio(download_path)

                        # Save transcription to the result bucket
                        result_bucket = 'transcription-file-output-bucket'
                        base_name = os.path.splitext(
                            os.path.basename(object_key))[0]
                        extension = os.path.splitext(object_key)[1]
                        folder_path = os.path.dirname(object_key)
                        result_key = ''

                        if 'chunks' in object_key:
                            result_key = os.path.join(
                                folder_path, f'{base_name}.txt')
                            s3.put_object(Bucket=result_bucket,
                                          Key=result_key, Body=transcription_text)

                            print(
                                f'Transcription saved to {result_bucket}/{result_key}')

                            # Check if this is the last chunk
                            if is_last_chunk(object_key, bucket_name):
                                # Combine all chunk files and perform cleanup
                                combine_and_clean_chunks(
                                    result_bucket, folder_path, result_bucket, base_name)
                        else:
                            result_key = f'{base_name}_{extension[1:]}.txt'
                            s3.put_object(Bucket=result_bucket,
                                          Key=result_key, Body=transcription_text)

                            print(
                                f'Transcription saved to {result_bucket}/{result_key}')

                        # Delete the original file from the S3 bucket
                        delete_s3_object(bucket_name, object_key)

    except Exception as ex:
        print(f"Error processing event: {ex}")
