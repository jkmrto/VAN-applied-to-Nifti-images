import os
from datetime import datetime
import sys
import tensorflow as tf

from lib import session_helper as session
from lib.utils import utils
from lib.data_loader import MRI_stack_NORAD
from lib.data_loader import mri_atlas
from lib.utils.os_aux import create_directories
from lib.vae import VAE
from scripts.vae_with_cv_GM_and_WM import session_settings


def init_session_folders(architecture, path_to_root):
    """
    This method will create inside the "out" folder a folder with the datetime
    of the execution of the neural net and with, with 3 folders inside it
    :return:
    """
    own_datetime = datetime.now().strftime(r"%d_%m_%_Y_%H:%M")
    iden_session = own_datetime + " arch: " + "_".join(map(str, (architecture)))
    path_session_folder = os.path.join(path_to_root,
                                       iden_session)

    path_to_images = os.path.join(path_session_folder, session.folder_images)
    path_to_logs = os.path.join(path_session_folder, session.folder_log)
    path_to_meta = os.path.join(path_session_folder, session.folder_meta)
    path_to_encoding_out = os.path.join(path_session_folder,
                                        session.folder_encoding_out)
    path_to_encoding_out_test = os.path.join(path_session_folder,
                                             session.folder_encoding_out_test)
    path_to_encoding_out_train = os.path.join(path_session_folder,
                                              session.folder_encoding_out_train)
    path_to_grad_desc_error = os.path.join(path_to_logs, "DescGradError")
    path_to_grad_desc_error_images = os.path.join(path_to_images,
                                                  "DescGradError")

    create_directories([path_session_folder, path_to_images,
                        path_to_logs, path_to_meta, path_to_grad_desc_error,
                        path_to_grad_desc_error_images, path_to_encoding_out,
                        path_to_encoding_out, path_to_encoding_out_test,
                        path_to_encoding_out_train])

    return path_session_folder, path_to_grad_desc_error, \
           path_to_grad_desc_error_images, path_to_encoding_out_test, \
           path_to_encoding_out_train


def auto_execute():
    # Autoenconder configuration
    hyperparams = {
        "batch_size": 16,
        "learning_rate": 5E-6,
        "dropout": 0.9,
        "lambda_l2_reg": 1E-5,
        "nonlinearity": tf.nn.elu,
        "squashing": tf.nn.sigmoid,
    }

    # Neural net architecture
    after_input_architecture = [1000, 800, 500, 100]

    # SESSION CONFIGURATION
    session_conf = {
        "cv_rate": 0.6,
        "bool_normalized": True,
        "regions_used": "68to117",
        "batch_size": 10,
        "max_iter": 2000,
    }

    # Selecting the GM folder
    #dict_norad = stack_NORAD.get_gm_stack()
    #path_to_root = session_settings.path_GM_folder
    #path_cv_index_folder = session_settings.path_cv_folder

    # Selecting the wM folder
    dict_norad = MRI_stack_NORAD.get_wm_stack()
    path_to_root = session_settings.path_WM_folder
    path_cv_index_folder = session_settings.path_cv_folder



    execute(dict_norad, hyperparams, session_conf, after_input_architecture,
            path_to_root, path_to_cv_index_folder=path_cv_index_folder)


def execute(voxels_values, hyperparams, session_conf, after_input_architecture,
            list_regions, path_to_root):

    per_region_results = {}

    region_voxels_index_per_region = mri_atlas.load_atlas_mri()


    path_session_folder, path_to_grad_desc_error, \
    path_to_grad_desc_error_images, \
    path_to_encoding_out_test, path_to_encoding_out_train \
            = init_session_folders(after_input_architecture, path_to_root)

    # LOOP OVER REGIONS
    for region_selected in list_regions:
        print("Region Nº {} selected".format(region_selected))
        voxels_index = region_voxels_index_per_region[region_selected]

        # First map and the normalize to the unit the value of the pixels
        # filtering voxels per region
        region_voxels_values_train = voxels_values['train'][:, voxels_index]
        region_voxels_values_test = voxels_values['test'][:, voxels_index]

        if session_conf['bool_normalized']:
            region_voxels_values_train, max_denormalize = \
                utils.normalize_array(region_voxels_values_train)
            region_voxels_values_test = region_voxels_values_test / max_denormalize

        architecture = [region_voxels_values_train.shape[1]]
        architecture.extend(after_input_architecture)

        tf.reset_default_graph()
        v = VAE.VAE(architecture, hyperparams,
                    path_to_session=path_session_folder)

        region_suffix = 'region_' + str(region_selected)

        v.train(region_voxels_values_train,
                max_iter=session_conf["max_iter"],
                save_bool=session_conf['save_meta_bool'],
                suffix_files_generated=region_suffix,
                iter_to_save=500, iters_to_show_error=session_conf['show_error_iter'])

        # Script para pintar
        print("Region {} Trained!".format(region_selected))

  #      session.plot_grad_desc_error_per_region(path_to_grad_desc_error,
  #                                              region_selected,
  #                                              path_to_grad_desc_error_images)

        # ENCODING PHASE
        # Encoding samples for the next step
        train_output = v.encode(region_voxels_values_train)
        test_output = v.encode(region_voxels_values_test)

        session.save_encoding_output_per_region(path_to_encoding_out_train,
                                                train_output, region_selected)
        session.save_encoding_output_per_region(path_to_encoding_out_test,
                                                test_output, region_selected)

        per_region_results[str(region_selected)] = {}

        per_region_results[region_selected]['train_output'] = train_output
        per_region_results[region_selected]['test_output'] = test_output

    return per_region_results


def execute_without_any_logs(region_to_flat_voxels_train_dict,
                             hyperparams, session_conf,
                             list_regions, path_to_root=None,
                             region_to_flat_voxels_test_dict=None,
                             explicit_iter_per_region=[]):

    per_region_results = {}

    # LOOP OVER REGIONS
    for region_selected in list_regions:
        print("Region Nº {} selected".format(region_selected))

        train_voxels = region_to_flat_voxels_train_dict[region_selected]
        test_voxels = region_to_flat_voxels_test_dict[region_selected]

        if session_conf['bool_normalized']:
            train_voxels, max_denormalize = \
                utils.normalize_array(train_voxels)
            test_voxels = test_voxels / max_denormalize

        architecture = [train_voxels.shape[1]]
        architecture.extend(session_conf["after_input_architecture"])

        tf.reset_default_graph()
        v = VAE.VAE(architecture, hyperparams)

        region_suffix = 'region_' + str(region_selected)

        v.train(train_voxels,
                max_iter=session_conf["max_iter"],
                suffix_files_generated=region_suffix,
                iter_to_save=500,
                iters_to_show_error=session_conf['show_error_iter'],
                save_bool=session_conf["save_meta_bool"])

        # Script para pintar
        print("Region {} Trained!".format(region_selected))

        # ENCODING PHASE
        # Encoding samples for the next step
        train_output = v.encode(train_voxels)
        test_output = v.encode(test_voxels)

        per_region_results[region_selected] = {}

        per_region_results[region_selected]['train_output'] = train_output
        per_region_results[region_selected]['test_output'] = test_output

    return per_region_results


def execute_saving_meta_without_cv(
    dic_region_to_flat_voxels, hyperparams, session_conf, after_input_architecture,
    list_regions, path_to_root, explicit_iter_per_region, session_prefix_name):

    own_datetime = datetime.now().strftime(r"%d_%m_%_Y_%H:%M")
    session_name = session_prefix_name + "_" + own_datetime

    path_to_session = os.path.join(path_to_root, session_name)
    create_directories([path_to_session])

    session.generate_predefined_session_descriptor(
        path_session_folder= path_to_session,
        vae_hyperparameters= hyperparams,
        configuration=session_conf
    )

    for region_selected in list_regions:
        print("Region Evaluated {}".format(region_selected))
        matrix_voxels = dic_region_to_flat_voxels[region_selected]

        architecture = [matrix_voxels.shape[1]]
        architecture.extend(after_input_architecture)

        tf.reset_default_graph()
        v = VAE.VAE(architecture, hyperparams,
                path_to_session=path_to_session)

        max_train_iter = session.get_adequate_number_iterations(
            region_selected, explicit_iter_per_region, session_conf["n_iters"])

        print("Numbers Iters requered {}".format(max_train_iter))
        out = v.train(X=matrix_voxels,
                      max_iter=max_train_iter,
                      iters_to_show_error=session_conf["show_error_iter"],
                      save_bool=True,
                      suffix_files_generated=
                      "region_{}".format(region_selected),
                      similarity_evaluation=True,
                      dump_losses_log=True,
                      iter_to_save=100)
        if out == -1:
            print("Region {} Training process failed!"
              "SGD doesnt converge".format(region_selected))
            print("Exiting, readjust parameter for that region")
            sys.exit(0)

        elif out == 0:
            print("Region {} Correctly Trained and Saved!".format(
                region_selected))

    return path_to_session
#auto_execute()
