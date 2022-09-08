from typing import Optional, Tuple

import numpy as np
import cv2

from qgis.PyQt.QtCore import pyqtSignal
from qgis.core import QgsVectorLayer
from qgis.gui import QgsMapCanvas
from qgis.core import QgsRasterLayer
from qgis.core import QgsTask
from qgis.core import QgsProject

from deep_segmentation_framework.common.processing_parameters.map_processing_parameters import MapProcessingParameters
from deep_segmentation_framework.processing import processing_utils, extent_utils
from deep_segmentation_framework.common.defines import IS_DEBUG
from deep_segmentation_framework.common.processing_parameters.inference_parameters import InferenceParameters
from deep_segmentation_framework.processing.map_processor import MapProcessor
from deep_segmentation_framework.processing.tile_params import TileParams

if IS_DEBUG:
    from matplotlib import pyplot as plt


class MapProcessorInference(MapProcessor):
    def __init__(self,
                 inference_parameters: InferenceParameters,
                 **kwargs):
        super().__init__(
            params=inference_parameters,
            **kwargs)
        self.inference_parameters = inference_parameters
        self.model_wrapper = inference_parameters.model
        self._result_img = None

    def get_result_img(self):
        return self._result_img

    def _run(self):
        final_shape_px = (self.img_size_y_pixels, self.img_size_x_pixels)
        full_result_img = np.zeros(final_shape_px, np.uint8)

        for tile_img, tile_params in self.tiles_generator():
            if self.isCanceled():
                return False

            tile_result = self._process_tile(tile_img)
            # plt.figure(); plt.imshow(tile_img); plt.show(block=False); plt.pause(0.001)
            # self._show_image(tile_result)
            tile_params.set_mask_on_full_img(
                tile_result=tile_result,
                full_result_img=full_result_img)

        full_result_img = processing_utils.erode_dilate_image(
            img=full_result_img,
            inference_parameters=self.inference_parameters)
        # plt.figure(); plt.imshow(full_result_img); plt.show(block=False); plt.pause(0.001)
        self._result_img = self.limit_extended_extent_image_to_base_extent_with_mask(full_img=full_result_img)
        self._create_vlayer_from_mask_for_base_extent(self._result_img)
        return True

    def limit_extended_extent_image_to_base_extent_with_mask(self, full_img):
        """
        Limit an image which is for extended_extent to the base_extent image.
        If a limiting polygon was used for processing, it will be also applied.
        :param full_img:
        :return:
        """
        # TODO look for some inplace operation to save memory
        # cv2.copyTo(src=full_img, mask=area_mask_img, dst=full_img)  # this doesn't work due to implementation details
        full_img = cv2.copyTo(src=full_img, mask=self.area_mask_img)

        b = self.base_extent_bbox_in_full_image
        result_img = full_img[b.y_min:b.y_max+1, b.x_min:b.x_max+1]
        return result_img

    def _create_vlayer_from_mask_for_base_extent(self, mask_img):
        # create vector layer with polygons from the mask image
        contours, hierarchy = cv2.findContours(mask_img, cv2.RETR_TREE, cv2.CHAIN_APPROX_SIMPLE)
        contours = processing_utils.transform_contours_yx_pixels_to_target_crs(
            contours=contours,
            extent=self.base_extent,
            rlayer_units_per_pixel=self.rlayer_units_per_pixel)
        features = []

        if len(contours):
            processing_utils.convert_cv_contours_to_features(
                features=features,
                cv_contours=contours,
                hierarchy=hierarchy[0],
                is_hole=False,
                current_holes=[],
                current_contour_index=0)
        else:
            pass  # just nothing, we already have an empty list of features

        vlayer = QgsVectorLayer("multipolygon", "model_output", "memory")
        vlayer.setCrs(self.rlayer.crs())
        prov = vlayer.dataProvider()

        color = vlayer.renderer().symbol().color()
        OUTPUT_VLAYER_COLOR_TRANSPARENCY = 80
        color.setAlpha(OUTPUT_VLAYER_COLOR_TRANSPARENCY)
        vlayer.renderer().symbol().setColor(color)
        # TODO - add also outline for the layer (thicker black border)

        prov.addFeatures(features)
        vlayer.updateExtents()
        QgsProject.instance().addMapLayer(vlayer)

    def _process_tile(self, tile_img: np.ndarray) -> np.ndarray:
        # TODO - create proper mapping for output channels
        result = self.model_wrapper.process(tile_img)

        # TODO - apply argmax classification and thresholding
        result_threshold = result > (self.inference_parameters.pixel_classification__probability_threshold * 255)

        return result_threshold
