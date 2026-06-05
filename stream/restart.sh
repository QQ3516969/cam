#!/bin/sh
sleep 3
while true; do
  ffmpeg -hide_banner -loglevel warning \
    -f v4l2 -input_format h264 -framerate 30 -video_size 1920x1080 -i /dev/video0 \
    -an -c:v copy \
    -f rtsp -rtsp_transport tcp rtsp://cam-stream:8554/cam
  echo "ffmpeg crashed, waiting 5s before restart..." >&2
  sleep 5
done