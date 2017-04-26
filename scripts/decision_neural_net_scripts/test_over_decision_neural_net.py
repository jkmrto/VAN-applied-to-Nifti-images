from lib.neural_net.decision_neural_net import DecisionNeuralNet
from lib.aux_functionalities.os_aux import create_directories
from lib.aux_functionalities.functions import print_dictionary
from sklearn import metrics
import numpy as np
import matplotlib.pyplot as plt
import tensorflow as tf
import settings
import os

id_autoencoder_session = "25_04_2017_20:51 arch: 1000_800_500_100"
output_project_folder = "out"
main_test_folder_autoencoder_session = "post_train"
svm_test_name = "second_test"
score_file_name = "patient_score_per_region.log"
root_session_folder_name = "neural_net-2017_04_26-00:21-116_58_1"
cv_session_folder_name = "cv_data"

path_to_session_folder = os.path.join(settings.path_to_project,
                                      output_project_folder,
                                      id_autoencoder_session,
                                      main_test_folder_autoencoder_session,
                                      svm_test_name,
                                      root_session_folder_name)
path_to_cv = os.path.join(path_to_session_folder, cv_session_folder_name)
path_to_test_labels = os.path.join(path_to_cv, "y_test.csv")
path_to_test_data = os.path.join(path_to_cv, "X_test.csv") # Data already normalized
X_test = np.genfromtxt(path_to_test_data, delimiter=',')
y_test = np.genfromtxt(path_to_test_labels, delimiter=',')
y_test = np.reshape(y_test, (y_test.shape[0], 1))

path_to_meta_files = os.path.join(path_to_session_folder, "meta")
path_to_main_results_output = os.path.join(path_to_session_folder, "post_train")
path_to_own_results_output = os.path.join(path_to_main_results_output, "TestUsingTestData")
path_to_results_file = os.path.join(path_to_own_results_output, "results.csv")
path_to_roc_png = os.path.join(path_to_own_results_output, "curva_roc.png")
path_to_resume_file = os.path.join(path_to_own_results_output, "resume.txt")
create_directories([path_to_main_results_output, path_to_own_results_output])

tf.reset_default_graph()
sess = tf.Session()
metafile = os.path.join(path_to_meta_files, "-100000.meta")
savefile = os.path.join(path_to_meta_files, "-100000")
new_saver = tf.train.import_meta_graph(metafile)
new_saver.restore(sess, savefile)

v = DecisionNeuralNet(root_path=path_to_session_folder,
                      meta_graph=savefile)
# Taking the first element of the list returned
y_obtained = v.forward_propagation(X_test, y_test)[0]


results = np.concatenate((y_test, y_obtained))

precision = metrics.average_precision_score(y_test, y_obtained)
auc = metrics.roc_auc_score(y_test, y_obtained)
output_dic = {"precision": precision,
              "area under the curve": auc}
print_dictionary(path_to_resume_file, output_dic)

[fpr, tpr, thresholds] = metrics.roc_curve(y_test, y_obtained)
np.savetxt(path_to_results_file, results, delimiter=',')
plt.plot(fpr, tpr, linestyle='--')
plt.savefig(path_to_roc_png)