import os
import sys
from datetime import datetime
from lib.utils import output_utils
import numpy as np
import tensorflow as tf
from lib.utils import utils3d
import lib.loss_function as loss
from lib.neural_net.layers import Dense
from lib.utils.functions import get_batch_from_samples_unsupervised
from lib.utils.math_utils import sample_gaussian
from lib.utils.os_aux import create_directories
from lib.utils.utils import compose_all
from lib.reconstruct_helpers import reconstruct_3d_image_from_flat_and_index
from lib.utils.output_utils import from_3d_image_to_nifti_file
from lib.utils.utils3d import reshape_from_3d_to_flat
from lib.utils.utils3d import reshape_from_flat_to_3d

class VAE():
    """Variational Autoencoder

    see: Kingma & Welling - Auto-Encoding Variational Bayes
    (http://arxiv.org/abs/1312.6114)
    """

    RESTORE_KEY = "restore"

    def __init__(self, architecture=None, hyperparams=None, meta_graph=None,
                 path_to_session=None, test_bool=False,
                 generate_tensorboard=False):

        self.session = tf.Session()
        self.hyper_params = hyperparams
        self.path_session_folder = path_to_session
        self.generate_tensorboard = generate_tensorboard
        print("architecture: {}".format(architecture))

        if not meta_graph:  # new model
            self.architecture = architecture
            self.hyper_params.update(hyperparams)

            if test_bool:
                print("Hyperparamers indicated: " + str(self.hyper_params))

            # path_to_session should be indicated if we want to create data
            # associated to the session such as the logs, and metagraphs
            # generated by sensor flow. It it is just a test session, in order
            # to test a feature, it is not necessary to indicate the path
            if None is not self.path_session_folder:
                self.init_session_folders()

            assert len(self.architecture) > 2, \
                "Architecture must have more layers! (input, 1+ hidden, latent)"
            # build graph
            handles = self._build_graph()
            for handle in handles:
                tf.add_to_collection(VAE.RESTORE_KEY, handle)
            self.session.run(tf.global_variables_initializer())

        else:  # restore saved model
            tf.train.import_meta_graph(meta_graph + ".meta").restore(self.session, meta_graph)
            handles = self.session.graph.get_collection_ref(VAE.RESTORE_KEY)

        (self.x_in, self.dropout_, self.z_mean, self.z_log_sigma,
         self.x_reconstructed, self.z_, self.x_reconstructed_,
         self.cost, self.lat_loss, self.gen_loss,
         self.global_step, self.train_op) = handles[0:12]

        self.__generate_tensorboard_files()

    def __generate_tensorboard_files(self):
        print("Generating Tensorboard {}".format(self.generate_tensorboard))

        if self.generate_tensorboard:
            if self.path_session_folder is None:
                print("It is not possible to generate Tensorflow graph without a"
                      "path session specified")
            else:
                print("Generating Tensorboard")
                tb_path = os.path.join(self.path_session_folder, "tb")
                writer = tf.summary.FileWriter(
                    tb_path,graph=tf.get_default_graph())

    def init_session_folders(self):
        """
        This method will create inside the "out" folder a folder with the datetime
        of the execution of the neural net and with, with 3 folders inside it
        :return:
        """
        self.path_to_images = os.path.join(self.path_session_folder, "images")
        self.path_to_logs = os.path.join(self.path_session_folder, "logs")
        self.path_to_meta = os.path.join(self.path_session_folder, "meta")
        self.path_to_grad_desc_error = os.path.join(self.path_to_logs, "DescGradError")
        self.path_to_losses_log = os.path.join(self.path_to_logs , "losses_logs")


        create_directories([self.path_session_folder, self.path_to_images,
                            self.path_to_logs, self.path_to_meta,
                            self.path_to_grad_desc_error,
                            self.path_to_losses_log])

    @property
    def step(self):
        """Train step"""
        return self.global_step.eval(session=self.session)

    def __build_cost_estimate(self, x_reconstructed, x_in, z_mean, z_log_sigma):

        # reconstruction loss: mismatch b/w x & x_reconstructed
        # binary cross-entropy -- assumes x & p(x|z) are iid Bernoullis
        with tf.variable_scope("Reconstruction_Cost"):
            rec_loss = loss.crossEntropy(x_reconstructed, x_in)

        with tf.variable_scope("Latent_Layer_Cost"):
        # Kullback-Leibler divergence: mismatch b/w approximate vs. imposed/true posterior
            kl_loss = loss.kullbackLeibler(z_mean, z_log_sigma)

        with tf.variable_scope("l2_regularization"):
            regularizers = [tf.nn.l2_loss(var) for var in self.session.graph.get_collection(
                "trainable_variables") if "weights" in var.name]
            l2_reg = self.hyper_params['lambda_l2_reg'] * tf.add_n(regularizers)

        # average over minibatch
        cost = tf.reduce_mean(rec_loss + kl_loss, name="vae_cost")
        cost += l2_reg

        return cost, kl_loss, rec_loss

    def _build_graph(self):
        with tf.name_scope("input"):
            x_in = tf.placeholder(tf.float32, shape=[None, self.architecture[0]], name="x")
            dropout = tf.placeholder_with_default(1., shape=[], name="dropout")

        # encoding / "recognition": q(z|x) ->  outer -> inner
        encoding = [Dense("coding", hidden_size, dropout, self.hyper_params['nonlinearity'])
                    for hidden_size in reversed(self.architecture[1:-1])]
        h_encoded = compose_all(encoding)(x_in)

        # latent distribution parametetrized by hidden encoding
        # z ~ N(z_mean, np.exp(z_log_sigma)**2)
        with tf.name_scope("Latent_layer"):
            z_mean = Dense("z_mean", self.architecture[-1], dropout)(h_encoded)
            z_log_sigma = Dense("z_log_sigma", self.architecture[-1], dropout)(h_encoded)

        with tf.name_scope("param_trick"):
            # kingma & welling: only 1 draw necessary as long as minibatch large enough (>100)
            z = sample_gaussian(z_mean, z_log_sigma)

        # decoding / "generative": p(x|z)
        decoding = [Dense("decod_", hidden_size, dropout, self.hyper_params['nonlinearity'])
                    for hidden_size in self.architecture[1:-1]]  # assumes symmetry

        # final reconstruction: restore original dims, squash outputs [0, 1]
        decoding.insert(0, Dense(  # prepend as outermost function
            "x_decoding", self.architecture[0], dropout, self.hyper_params['squashing']))
        x_reconstructed = tf.identity(compose_all(decoding)(z), name="x_reconstructed")

        with tf.variable_scope("Cost_estimation"):
            cost, kl_loss, rec_loss = self.__build_cost_estimate(x_reconstructed, x_in, z_mean, z_log_sigma)

        # optimization
        global_step = tf.Variable(0, trainable=False)
        with tf.variable_scope("Adam_optimizer"):
            optimizer = tf.train.AdamOptimizer(self.hyper_params['learning_rate'])
            tvars = tf.trainable_variables()
            grads_and_vars = optimizer.compute_gradients(cost, tvars)
            clipped = [(tf.clip_by_value(grad, -5, 5), tvar)  # gradient clipping
                       for grad, tvar in grads_and_vars]
            train_op = optimizer.apply_gradients(clipped, global_step=global_step,
                                                 name="minimize_cost")

        # ops to directly explore latent space
        # defaults to prior z ~ N(0, I)
        with tf.variable_scope("Regenerator"):
            z_ = tf.placeholder(tf.float32, shape=[None, self.architecture[-1]], name="latent_in")

            x_reconstructed_ = tf.identity(compose_all(decoding)(z_),
                                          name="x_reconstructed")

        return (x_in, dropout, z_mean, z_log_sigma, x_reconstructed,
                z_, x_reconstructed_, cost, kl_loss, rec_loss, global_step, train_op)

    def encode(self, x):
        """Probabilistic encoder from inputs to latent distribution parameters;
        a.k.a. inference network q(z|x)
        """
        # np.array -> [float, float]

        feed_dict = {self.x_in: x}
        output = self.session.run([self.z_mean, self.z_log_sigma], feed_dict=feed_dict)
        out_dict = {"mean": output[0],
                   "stdev": output[1]}
        return out_dict

    def decode(self, zs=None):
        """Generative decoder from latent space to reconstructions of input space;
        a.k.a. generative network p(x|z)
        """
        # (np.array | tf.Variable) -> np.array
        feed_dict = dict()
        if zs is not None:
            is_tensor = lambda x: hasattr(x, "eval")
            zs = (self.session.run(zs) if is_tensor(zs) else zs) # coerce to np.array
            feed_dict.update({self.z_: zs})
        # else, zs defaults to draw from conjugate prior z ~ N(0, I)
        return self.session.run(self.x_reconstructed_, feed_dict=feed_dict)

    def vae(self, x):
        """End-to-end autoencoder"""
        # np.array -> np.array
        return self.decode(sample_gaussian(*self.encode(x)))

    def save(self, saver, suffix_file_saver_name):

        outfile = os.path.join(self.path_to_meta, suffix_file_saver_name)
        saver.save(self.session, outfile, global_step=self.step)

    def training_end_output(self, last_avg_cost):

        print("final avg cost %1.5f" % (last_avg_cost))
        now = datetime.now().isoformat()[11:]
        print("------- Training end: {} -------\n".format(now))

    def train(self, X, max_iter=np.inf, save_bool=False, suffix_files_generated=" ",
              iter_to_save=1000, iters_to_show_error=100,
              bool_log_grad_desc_error=False, sgd_3dimages=None,
              similarity_evaluation=False, dump_losses_log=False):
        """

        :param X: sh[n_samples, n_voxeles]
        :param max_iter:
        :param save_bool:
        :param suffix_files_generated:
        :param iter_to_save:
        :param iters_to_show_error:
        :param bool_log_grad_desc_error:
        :param sgd_3dimages:
        :return:
        """
        saver = tf.train.Saver(tf.global_variables()) if save_bool else None
        err_train = 0

        # Temporal Evolution of Region Initialization
        sgd_3dimages = self.__initialize_sgd_3d_images_folder(sgd_3dimages, X)

        if dump_losses_log:
            losses_log_file = self.__generate_losses_log_file(
                suffix=suffix_files_generated,
                similarity_evaluation=similarity_evaluation)
        else:
            losses_log_file = None

        # Gradient Descent log
        gradient_descent_log = None
        if bool_log_grad_desc_error:
            path_to_file = os.path.join(self.path_to_grad_desc_error,
                                    suffix_files_generated + ".log")
            gradient_descent_log = open(path_to_file, "w")


        try:
            now = datetime.now().isoformat()[11:]
            print("------- Training begin: {} -------\n".format(now))
            i = 0
            last_avg_cost = 0
            while True:  # Se ejecuta hasta condicion i>max_iter -> break

                # batch selector
                x = get_batch_from_samples_unsupervised(
                    X, self.hyper_params['batch_size'])

                # Autoencoder Session
                feed_dict = {self.x_in: x, self.dropout_: self.hyper_params['dropout']}
                fetches = [self.x_reconstructed, self.cost, self.lat_loss,
                           self.gen_loss, self.global_step, self.train_op]
                x_reconstructed, cost, lat_loss, gen_loss, i, _ = \
                    self.session.run(fetches, feed_dict)

                err_train += cost

                if bool_log_grad_desc_error:
                    gradient_descent_log.write("{0},{1}\n".format(i, cost))

                if i % iters_to_show_error == 0:
                    self.__log_loss_data(
                        iter_index=i,
                        gen_loss=np.mean(gen_loss),
                        lat_loss=np.mean(lat_loss),
                        learning_rate=self.hyper_params['learning_rate'],
                        images_flat=X,
                        losses_log_file=losses_log_file,
                        similarity_evaluation=similarity_evaluation)


                    if sgd_3dimages is not None:
                        self.__generate_and_save_temp_3d_images(
                            sgd_3dimages, suffix="iter_{}".format(i))

                if i % iter_to_save == 0:
                    if save_bool:
                        self.save(saver, suffix_files_generated)

                if i >= max_iter:
                    self.training_end_output(last_avg_cost)

                    if bool_log_grad_desc_error:
                        if self.path_session_folder is not None:
                            gradient_descent_log.close()

                    if save_bool and self.path_session_folder is not None:
                        self.save(saver, suffix_files_generated)

                    break

        except(KeyboardInterrupt):
            if bool_log_grad_desc_error:
                gradient_descent_log.close()

            print("final avg cost (@ step {} = epoch {}): {}".format(
                i, X.train.epochs_completed, err_train / i))
            now = datetime.now().isoformat()[11:]
            print("------- Training end: {} -------\n".format(now))
            sys.exit(0)

    def __initialize_sgd_3d_images_folder(self, sgd_3dimages, X, logs=True):
        if sgd_3dimages is not None:
            if self.path_session_folder is not None:

                path_sgd3d_images = os.path.join(self.path_to_images, "sgd_3dimages")
                path_original_3dimg = os.path.join(path_sgd3d_images, "original")
                create_directories([path_sgd3d_images])

                sample_voxels = X[sgd_3dimages["sample"], :]
                sample_stack = np.vstack((sample_voxels, sample_voxels))

                img3d = reconstruct_3d_image_from_flat_and_index(
                    image_flatten=sample_voxels,
                    voxels_index=sgd_3dimages["voxels_location"],
                    imgsize=sgd_3dimages["full_brain_size"],
                    reshape_kind=sgd_3dimages["reshape_kind"])

                img3d_segmented = utils3d.get_3dimage_segmented(img3d)

                from_3d_image_to_nifti_file(
                    path_to_save=path_original_3dimg,
                    image3d=img3d_segmented)

                if logs:
                    print("INITIALIZATION LOGS SGD TEMP 3D IMAGES")
                    print("path sgd 3d iamges: {}".format(path_sgd3d_images))
                    print("sample stack sgd images: {}".format(sample_stack.shape))
                    print("sample selected: {}".format(sgd_3dimages["sample"]))
                    print("full_brain_size: {}".format(sgd_3dimages["full_brain_size"]))
                    print("region_size: {}".format(sgd_3dimages["region_size"]))
                    print("shape voxels_location: {}".format(sgd_3dimages["voxels_location"].shape))
                    print("reshape_kind: {}".format(sgd_3dimages["reshape_kind"]))

                sgd_3dimages["path"] = path_sgd3d_images
                sgd_3dimages["sample_stack"] = sample_stack
                return sgd_3dimages

            else:
                raise ValueError('It is not possible to store the temp 3d images'
                                 'because it was not specified a folder for the session')

    def __generate_and_save_temp_3d_images(self, sgd_3dimages,suffix):
        # Init Parameters
        path = sgd_3dimages["path"]
        stack_sample_to_dump = sgd_3dimages["sample_stack"]

        # Autoencoder session
        feed_dict = {self.x_in: stack_sample_to_dump}
        generated_test = self.session.run(
            self.x_reconstructed[1, :],
            feed_dict=feed_dict)

        img3d = reconstruct_3d_image_from_flat_and_index(
            image_flatten=generated_test,
            voxels_index=sgd_3dimages["voxels_location"],
            imgsize=sgd_3dimages["full_brain_size"],
            reshape_kind=sgd_3dimages["reshape_kind"])
        img3d_segmented = utils3d.get_3dimage_segmented(img3d)

        file_path = os.path.join(path, suffix + "_{}".format(sgd_3dimages["region"]))
        output_utils.from_3d_image_to_nifti_file(file_path, img3d_segmented)

    def __log_loss_data(self, iter_index, gen_loss, lat_loss, learning_rate,
                        images_flat, losses_log_file, similarity_evaluation):

        if similarity_evaluation is not None:
            # Generate %similarity in reconstruction
            similarity_score, mse_score = \
                self.__full_reconstruction_error_evaluation(images_flat=images_flat)

            print("iter {0}: genloss {1}, latloss {2}, "
                  "learning_rate {3}, Similarity Score: {4},"
                  "MSE {5}".format(
                iter_index, gen_loss, lat_loss,
                learning_rate, similarity_score, mse_score))

            if losses_log_file is not None:
                losses_log_file.write("{0},{1},{2},{3},{4},{5}\n".format(
                    iter_index, gen_loss, lat_loss,
                    learning_rate, similarity_score, mse_score))

        else:
            print("iter {0}: genloss {1}, latloss {2}, learning_rate {3}".format(
                    iter_index, gen_loss, lat_loss,learning_rate))

            if losses_log_file is not None:
                losses_log_file.write("{0},{1},{2},{3}\n".format(
                    iter_index, gen_loss, lat_loss, learning_rate))

    def __full_reconstruction_error_evaluation(self, images_flat):

        n_samples = images_flat.shape[0]
        feed_dict = {self.x_in: images_flat}
        bool_logs = False

        reconstructed_images = self.session.run(
            self.x_reconstructed,
            feed_dict=feed_dict)

        diff_matrix = np.subtract(images_flat, reconstructed_images)

        # similarity_evaluation
        total_diff = diff_matrix.sum()
        similarity_evaluation = abs(
            total_diff / np.array(images_flat.shape).prod())

        # MSE

        square_diff_matrix = np.power(diff_matrix, 2)
        mse_over_samples = square_diff_matrix.sum() / n_samples

        if bool_logs:
            print("Similarity {}%".format(similarity_evaluation))

        return similarity_evaluation, mse_over_samples

    def __generate_losses_log_file(self, suffix, similarity_evaluation):

        path_to_file = \
            os.path.join(self.path_to_losses_log,
                         "{0}.txt".format(suffix))
        file = open(path_to_file, "w")

        if similarity_evaluation:
            file.write("{0},{1},{2},{3},{4}, {5}".format(
                "iteration", "generative loss", "latent layer loss",
                "learning rate", "similarity score", "MSE error over samples\n"))
        else:
            file.write("{0},{1},{2},{3}".format(
                "iteration", "generative loss", "latent layer loss",
                "learning rate\n"))

        return file
