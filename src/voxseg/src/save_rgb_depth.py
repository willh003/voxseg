#!/usr/bin/env python
import rospy
from rospy import Publisher
from sensor_msgs.msg import CompressedImage, Image
from tf2_msgs.msg import TFMessage
from message_filters import ApproximateTimeSynchronizer, Subscriber
import cv2
from cv_bridge import CvBridge, CvBridgeError
import numpy as np
from modules.data import UnalignedData
from modules.config import BATCH_SIZE
from geometry_msgs.msg import TransformStamped 
import yaml

# Method cutout from Wild Visual Navigation--------------------#
from typing import Sequence
from liegroups import SE3, SO3
def transformation_matrix_of_pose(pose : Sequence[float]):
    """Convert a translation and rotation into a 4x4 transformation matrix.
 
    Args:
        pose (float subscriptable @ 0..6): A 7 element array representing the pose [tx,ty,tz,q0,qx,qy,qz,qw].

    Returns:
        4x4 Transformation Matrix \in SE3 following the ordering convention specified"""
    quat = np.array(pose[3:])
    quat = quat / np.linalg.norm(quat)
    matrix = SE3(rot=SO3.from_quaternion(quat, ordering='xyzw'), trans=pose[:3]).as_matrix() # Check order (wxyz looks correct for orbit footpath)
    matrix = matrix.astype(np.float32)
    return matrix
#--------------------------------------------------------------#

def pose_from_yaml(tf_yaml) -> np.ndarray:
    tx = tf_yaml["transform"]["translation"]["x"]
    ty = tf_yaml["transform"]["translation"]["y"]
    tz = tf_yaml["transform"]["translation"]["z"]
    qx = tf_yaml["transform"]["rotation"]["x"]
    qy = tf_yaml["transform"]["rotation"]["y"]
    qz = tf_yaml["transform"]["rotation"]["z"]
    qw = tf_yaml["transform"]["rotation"]["w"]
    return np.array([tx,ty,tz,qx,qy,qz,qw])


class ImageSaver:
    def __init__(self):
        self.bridge = CvBridge()
        self.data = UnalignedData(device='cuda', batch_size=BATCH_SIZE)
        

        # Create subscribers for the image topics
        self.rgb_sub = Subscriber("/wide_angle_camera_front/image_color_rect/compressed", CompressedImage)
        self.depth_sub = Subscriber("/depth_camera_front_upper/depth/image_rect_raw", Image)

        # Cannot just use tf_sub as it has no header...
        # instead we need to add an intermediate sub/pub trio. 
        self.tf_main_sub = Subscriber("/tf", TFMessage, callback=self.publish_tf_list_to_specific_tfs)

        # Create subscribers for the tf topics
        self.tf_odom_sub = Subscriber("/tf_odom", TransformStamped)
        self.tf_rgb_sub = Subscriber("/tf_rgb", TransformStamped)
        self.tf_depth_sub = Subscriber("/tf_depth", TransformStamped)

        # Publishers for the individual tf topics
        self.tf_odom_pub = Publisher("/tf_odom", TransformStamped, queue_size=100) # NOTE: Arbitrary number
        self.tf_rgb_pub = Publisher("/tf_rgb", TransformStamped, queue_size=100)
        self.tf_depth_pub = Publisher("/tf_depth", TransformStamped, queue_size=100)

        # Synchronize the topics
        ats = ApproximateTimeSynchronizer([self.rgb_sub, self.depth_sub, 
                                           self.tf_odom_sub, self.tf_rgb_sub, self.tf_depth_sub], 
                                           slop=0.1, queue_size=100) # NOTE: 0.1 default, 100 Arbitrary number
        ats.registerCallback(self.callback)
    
    def publish_tf_list_to_specific_tfs(self, tf_msg : TFMessage):
        # get camera tfs
        tf_data = yaml.safe_load(tf_msg)

        # find transforms of interest        
        rgb_frame_id = "wide_angle_camera_front_camera_parent" #rgb_img
        depth_frame_id = "depth_camera_front_upper_depth_optical_frame" #depth_img
        base_frame_id = "base"

        # Need to get (child_frame_id == ^^above)
        # For each child, publish TransformStamped

        print("B")
        for entry in tf_data:
            if entry.get("child_frame_id") == rgb_frame_id:
                rgb_yaml = entry["transform"]
                break
            elif entry.get("child_frame_id") == depth_frame_id:
                depth_yaml = entry["transform"]
                break
            elif entry.get("child_frame_id") == base_frame_id:
                base_yaml = entry["transform"]
                break

    def callback(self, rgb_image:CompressedImage, depth_image:Image, 
                 tf_odom:TransformStamped, tf_rgb:TransformStamped, tf_depth:TransformStamped):
        try:
            ### NOTE: THIS STUFF IS OKAY ####
            # Convert RGB compressed image to OpenCV format, then to numpy
            rgb_img = self.bridge.compressed_imgmsg_to_cv2(rgb_image, desired_encoding="bgr8")
            #cv2.imwrite(f'rgb_{rgb_msg.header.stamp}.jpg', rgb_img)
            rgb_img_np = np.array(rgb_img)
            
            # Convert depth image to OpenCV format, then to numpy
            depth_img = self.bridge.imgmsg_to_cv2(depth_image, desired_encoding="passthrough")
            #cv2.imwrite(f'depth_{depth_msg.header.stamp}.png', depth_img)
            depth_img_np = np.array(depth_img)
            ### END OKAY ####
            
            ### get local transforms
            # rgb_in_base_tf = transformation_matrix_of_pose(pose_from_yaml(rgb_yaml))
            # depth_in_base_tf = transformation_matrix_of_pose(pose_from_yaml(depth_yaml))
            # base_in_odom_tf = transformation_matrix_of_pose(pose_from_yaml(base_yaml))

            ### combine to get global transforms
            rgb_extrinsics = None #base_in_odom_tf @ rgb_in_base_tf
            depth_extrinsics = None #base_in_odom_tf @ depth_in_base_tf

            ### pass to Data object
            self.data.add_depth_image(rgb_img_np, depth_img_np, rgb_extrinsics, depth_extrinsics)
            print(f'Depth Image {len(self.data.all_images)} Received')
        
            rospy.loginfo("Saved synchronized images with timestamp: %s", rgb_image.header.stamp)
            
        except CvBridgeError as e:
            print("CVE")
            rospy.logerr(e)
        except ValueError as e:
            print("VE")
            rospy.logerr(e)


if __name__ == '__main__':

    rospy.init_node('image_saver_node')
    rospy.loginfo("Set up (rgb,depth) image saver node")
    ImageSaver()
    rospy.spin()
