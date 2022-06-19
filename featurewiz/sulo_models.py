import numpy as np
import pandas as pd
import math
from collections import Counter
from sklearn.tree import DecisionTreeClassifier, DecisionTreeRegressor
from sklearn.ensemble import RandomForestClassifier, RandomForestRegressor, AdaBoostClassifier
from sklearn.ensemble import ExtraTreesClassifier
from sklearn.linear_model import LogisticRegression, LogisticRegressionCV
from sklearn.multiclass import OneVsRestClassifier, OneVsOneClassifier
from sklearn.metrics import accuracy_score
from sklearn.model_selection import KFold, StratifiedKFold
import time
from sklearn.cluster import KMeans
from matplotlib.pyplot import figure
from sklearn.metrics.cluster import normalized_mutual_info_score
from sklearn.metrics import balanced_accuracy_score
from sklearn.ensemble import VotingRegressor, VotingClassifier
import copy
from sklearn.base import BaseEstimator, TransformerMixin, RegressorMixin, clone
from sklearn.base import ClassifierMixin
from imblearn.over_sampling import SMOTENC, ADASYN
from imblearn.over_sampling import SMOTE, SVMSMOTE
from imblearn.combine import SMOTETomek 
import lightgbm
from lightgbm import LGBMClassifier, LGBMRegressor
from sklearn.multioutput import MultiOutputRegressor, MultiOutputClassifier
from sklearn.multioutput import ClassifierChain, RegressorChain
import scipy as sp
import pdb
from sklearn.semi_supervised import LabelPropagation
from sklearn.ensemble import BaggingClassifier
from sklearn.pipeline import Pipeline
from sklearn.impute import SimpleImputer, MissingIndicator
from sklearn.preprocessing import StandardScaler, OneHotEncoder, LabelEncoder
from sklearn.compose import ColumnTransformer

from .featurewiz import get_class_distribution

class SuloClassifier(BaseEstimator, ClassifierMixin):
    """
    SuloClassifier works really fast and very well for all kinds of datasets.
    It works on small as well as big data. It works in multi-class as well as multi-labels.
    It works on regular balanced data as well as imbalanced data sets.
    The reason it works so well is that it is an ensemble of highly tuned models.
    You don't have to send any inputs but if you wanted to, you can send in two inputs:
    n_estimators: number of models you want in the final ensemble.
    base_estimator: base model you want to train in each of the ensembles.
    If you want, you can igore both these inputs and it will automatically choose these.
    It is fully compatible with scikit-learn pipelines and other models.
    """
    def __init__(self, base_estimator=None, n_estimators=None, pipeline=True, weights=False, verbose=0):
        self.n_estimators = n_estimators
        self.base_estimator = base_estimator
        self.pipeline = pipeline
        self.weights = weights
        self.verbose = verbose
        self.models = []
        self.multi_label =  False
        self.max_number_of_classes = 1
        self.scores = []
        self.classes = []

    def fit(self, X, y):
        seed = 42
        shuffleFlag = True
        modeltype = 'Classification'
        start = time.time()
        # Use KFold for understanding the performance
        if self.weights:
            print('Remember that using class weights will wrongly skew predict_probas from any classifier')
        class_weights = get_class_weights(y, verbose=self.verbose)
        ### Remember that putting class weights will totally destroy predict_probas ###
        self.classes = print_flatten_dict(class_weights)
        scale_pos_weight = get_scale_pos_weight(y)
        #print('Class weights = %s' %class_weights)
        ## Don't change this since it gives an error ##
        metric  = 'auc'
        ### don't change this metric and eval metric - it gives error if you change it ##
        eval_metric = 'auc'
        row_limit = 10000
        ################          P I P E L I N E        ##########################
        numeric_transformer = Pipeline(
            steps=[("imputer", SimpleImputer(strategy="mean", add_indicator=True)), ("scaler", StandardScaler())]
        )

        categorical_transformer_low = Pipeline(
            steps=[
                ("imputer", SimpleImputer(strategy="constant", fill_value="missing", add_indicator=True)),
                ("encoding", OneHotEncoder(handle_unknown="ignore", sparse=False)),
            ]
        )

        categorical_transformer_high = Pipeline(
            steps=[
                ("imputer", SimpleImputer(strategy="constant", fill_value="missing", add_indicator=True)),
                ("encoding", LabelEncoder()),
            ]
        )

        numeric_features = X.select_dtypes(include=[np.number]).columns
        categorical_features = X.select_dtypes(include=["object"]).columns

        categorical_low, categorical_high = get_cardinality(X, categorical_features)
        preprocessor = ColumnTransformer(
            transformers=[
                ("numeric", numeric_transformer, numeric_features),
                ("categorical_low", categorical_transformer_low, categorical_low),
                ("categorical_high", categorical_transformer_high, categorical_high),
            ]
        )
        ####################################################################################
        if isinstance(y, pd.DataFrame):
            if len(y.columns) >= 2:
                number_of_classes = num_classes(y)
                for each_i in y.columns:
                    number_of_classes[each_i] = int(number_of_classes[each_i] - 1)
                max_number_of_classes = np.max(list(number_of_classes.values()))
            else:
                number_of_classes = int(num_classes(y) - 1)
                max_number_of_classes = np.max(number_of_classes)
        else:
            number_of_classes = int(num_classes(y) - 1)
            max_number_of_classes = np.max(number_of_classes)
        data_samples = X.shape[0]
        self.max_number_of_classes = max_number_of_classes
        if self.n_estimators is None:
            if data_samples <= row_limit:
                self.n_estimators = min(5, int(2.5*np.log10(data_samples)))
            else:
                self.n_estimators = 4
            if self.verbose:
                print('Number of estimators = %d' %self.n_estimators)
        model_name = 'lgb'
        num_splits = self.n_estimators
        kfold = KFold(n_splits=num_splits, random_state=seed, shuffle=shuffleFlag)
        ##### This is where we check if y is single label or multi-label ##
        if isinstance(y, pd.DataFrame):
            ###############################################################
            ### This is for Multi-Label problems only #####################
            ###############################################################
            targets = y.columns.tolist()
            if is_y_object(y):
                print('Cannot perform classification using object or string targets. Please convert to numeric and try again.')
                return self
            if len(targets) > 1:
                self.multi_label = y.columns.tolist()
                ### You need to initialize the class before each run - otherwise, error!
                if self.base_estimator is None:
                    if self.max_number_of_classes <= 1:
                        ##############################################################
                        ###   This is for Binary Classification problems only ########
                        ##############################################################
                        if y.shape[0] <= row_limit:
                            if (X.dtypes==float).all():
                                print('    Selecting Label Propagation since it will work great for this dataset...')
                                print('        however it will skew probabilities and show lower ROC AUC score than normal.')
                                self.base_estimator =  LabelPropagation()
                                model_name = 'other'
                            else:
                                if self.verbose:
                                    print('    Selecting Bagging Classifier for this dataset...')
                                self.base_estimator = BaggingClassifier(n_estimators=150)
                                model_name = 'bg'
                        else:
                            self.base_estimator = LGBMClassifier(is_unbalance=True, learning_rate=0.3, 
                                                    max_depth=10, metric=metric,
                                                    #num_class=self.max_number_of_classes,
                                                    n_estimators=100,  num_leaves=84, 
                                                    #objective='binary',
                                                    boosting_type ='goss', scale_pos_weight=None)                    
                    else:
                        #############################################################
                        ###   This is for Multi Classification problems only ########
                        ### Make sure you don't put any class weights here since it won't work in multi-labels ##
                        ##############################################################
                        if y.shape[0] <= row_limit:
                            self.base_estimator = LGBMClassifier(is_unbalance=False, learning_rate=0.3,
                                                    max_depth=10, 
                                                    #metric='multi_logloss',
                                                    #num_class=self.max_number_of_classes,
                                                    #objective='multiclass',
                                                    n_estimators=100,  num_leaves=84, 
                                                    boosting_type ='goss', scale_pos_weight=None,
                                                    class_weight=None, verbose=-1)
                            print('    Selecting Label Propagation since it will work great for this dataset...')
                            print('        however it will skew probabilities and show lower ROC AUC score than normal.')
                            self.base_estimator =  LabelPropagation()
                            model_name = 'other'
                        else:
                            self.base_estimator = LGBMClassifier(bagging_seed=1337,
                                                   data_random_seed=1337,
                                                   drop_seed=1337,
                                                   feature_fraction_seed=1337,
                                                   max_depth=3,
                                                   n_estimators=150, seed=1337,
                                                   verbose=-1)
                
                for i, (train_index, test_index) in enumerate(kfold.split(X)):
                    start_time = time.time()
                    # Split data into train and test based on folds          
                    if isinstance(y, pd.Series) or isinstance(y, pd.DataFrame):
                        y_train, y_test = y.iloc[train_index], y.iloc[test_index]                
                    else:
                        y_train, y_test = y[train_index], y[test_index]

                    if isinstance(X, pd.DataFrame):
                        x_train, x_test = X.iloc[train_index], X.iloc[test_index]
                    else:
                        x_train, x_test = X[train_index], X[test_index]

                    ###### Do this only the first time ################################################
                    if i == 0:
                        ### It does not make sense to do hyper-param tuning for multi-label models ##
                        ###    since ClassifierChains do not have many hyper params #################
                        #self.base_estimator = rand_search(self.base_estimator, x_train, y_train, 
                        #                        model_name, verbose=self.verbose)
                        #print('    hyper tuned base estimator = %s' %self.base_estimator)

                        if self.max_number_of_classes <= 1:
                            est_list = [ClassifierChain(self.base_estimator, order="random", cv=3, random_state=i) 
                                        for i in range(num_splits)] 
                            if self.verbose:
                                print('Fitting a %s for %s targets with MultiOutputClassifier. This will take time...' %(
                                            str(self.base_estimator).split("(")[0],y.shape[1]))
                        else:
                            ### You must use multioutputclassifier since it is the only one predicts probas correctly ##
                            est_list = [MultiOutputClassifier(self.base_estimator)#, order="random", random_state=i) 
                                        for i in range(num_splits)] 
                            if self.verbose:
                                print('Fitting a %s for %s targets with MultiOutputClassifier. This will take time...' %(
                                            str(self.base_estimator).split("(")[0],y.shape[1]))

                    # Initialize model with your supervised algorithm of choice
                    model = est_list[i]

                    # Train model and use it to train on the fold
                    if self.pipeline:
                        ### This is only with a pipeline ########
                        pipe = Pipeline(
                            steps=[("preprocessor", preprocessor), ("classifier", model)]
                        )

                        pipe.fit(x_train, y_train)
                        self.models.append(pipe)

                        # Predict on remaining data of each fold
                        preds = pipe.predict(x_test)

                    else:
                        #### This is without a pipeline ###
                        model.fit(x_train, y_train)
                        self.models.append(model)

                        # Predict on remaining data of each fold
                        preds = model.predict(x_test)


                    # Use best classification metric to measure performance of model
                    #score = balanced_accuracy_score(y_test, preds)
                    
                    score = print_accuracy(targets, y_test, preds, verbose=self.verbose)
                    print("    Fold %s: Average OOF Score: %0.0f%%" %(i+1, 100*score))
                    self.scores.append(score)
                    
                    # Finally, check out the total time taken
                    end_time = time.time()
                    timeTaken = end_time - start_time
                    print("Time Taken for fold %s: %0.0f (seconds)" %(i+1, timeTaken))

                # Compute average score
                averageAccuracy = sum(self.scores)/len(self.scores)
                if self.verbose:
                    print("Average Balanced Accuracy of %s-model SuloClassifier: %0.0f%%" %(
                                        self.n_estimators, 100*averageAccuracy))
                end = time.time()
                timeTaken = end - start
                print("Time Taken overall: %0.0f (seconds)" %(timeTaken))
                return self
        ########################################################
        #####  This is for Single Label Classification problems 
        ########################################################
        if self.base_estimator is None:
            if data_samples <= row_limit:
                ### For small datasets use RFC for Binary Class   ########################
                if number_of_classes <= 1:
                    ### For binary-class problems use RandomForest or the faster ET Classifier ######
                    #self.base_estimator = ExtraTreesClassifier(n_estimators=50, max_depth=2,
                    #                random_state=0, class_weight=class_weights)
                    #model_name = 'rf'
                    if (X.dtypes==float).all():
                        print('    Selecting Label Propagation since it will work great for this dataset...')
                        print('        however it will skew probabilities and show lower ROC AUC score than normal.')
                        self.base_estimator =  LabelPropagation()
                        model_name = 'other'
                    else:
                        if self.verbose:
                            print('    Selecting Bagging Classifier for this dataset...')
                        ### The Bagging classifier outperforms ETC most of the time ####
                        self.base_estimator = BaggingClassifier(n_estimators=20)
                        model_name = 'bg'
                else:
                    ### For small datasets use LGBM for Multi Class   ########################
                    #self.base_estimator = ExtraTreesClassifier(n_estimators=50, max_depth=2,
                    #                random_state=0, class_weight=class_weights)
                    #model_name = 'rf'
                    ### For multi-class problems use Label Propagation which is faster and better ##
                    if (X.dtypes==float).all():
                        print('    Selecting Label Propagation since it will work great for this dataset...')
                        print('        however it will skew probabilities and show lower ROC AUC score than normal.')
                        self.base_estimator =  LabelPropagation()
                        model_name = 'other'
                    else:
                        if self.verbose:
                            print('    Selecting Bagging Classifier for this dataset...')
                        self.base_estimator = BaggingClassifier(n_estimators=20)
                        model_name = 'bg'
                    #self.base_estimator = LGBMClassifier(is_unbalance=False, learning_rate=0.3,
                    #                        max_depth=10, metric='multi_logloss',
                    #                        n_estimators=130, num_class=number_of_classes, num_leaves=84, objective='multiclass',
                    #                        boosting_type ='goss', scale_pos_weight=None,class_weight=class_weights)
            else:
                ### For large datasets use LGBM  ########################
                if number_of_classes <= 1:
                    #self.base_estimator = LGBMClassifier(n_estimators=250, random_state=99, 
                    #            boosting_type ='goss', scale_pos_weight=scale_pos_weight)
                    self.base_estimator = LGBMClassifier(is_unbalance=True, learning_rate=0.3, 
                                            max_depth=10, metric=metric,
                                            n_estimators=230, num_class=number_of_classes, num_leaves=84, objective='binary',
                                            boosting_type ='goss', scale_pos_weight=None)
                                
                else:
                    #self.base_estimator = LGBMClassifier(n_estimators=250, random_state=99,
                    #                   boosting_type ='goss', class_weight=class_weights)        
                    if self.weights:
                        class_weights = None
                    self.base_estimator = LGBMClassifier(is_unbalance=False, learning_rate=0.3,
                                            max_depth=10, metric='multi_logloss',
                                            n_estimators=230, num_class=number_of_classes, num_leaves=84, objective='multiclass',
                                            boosting_type ='goss', scale_pos_weight=None,class_weight=class_weights)
        else:
            model_name = 'other'

        est_list = num_splits*[self.base_estimator]
        
        ### if there is a need to do SMOTE do it here ##
        smote = False
        #list_classes = return_minority_classes(y)
        #if not list_classes.empty:
        #    smote = True
        #### For now, don't do SMOTE since it is making things really slow ##
        
        # Perform CV
        for i, (train_index, test_index) in enumerate(kfold.split(X)):
            # Split data into train and test based on folds          
            if isinstance(y, pd.Series) or isinstance(y, pd.DataFrame):
                y_train, y_test = y.iloc[train_index], y.iloc[test_index]                
            else:
                y_train, y_test = y[train_index], y[test_index]

            if isinstance(X, pd.DataFrame):
                x_train, x_test = X.iloc[train_index], X.iloc[test_index]
            else:
                x_train, x_test = X[train_index], X[test_index]

            # Convert the data into numpy arrays
            #if not isinstance(x_train, np.ndarray):
            #    x_train, x_test = x_train.values, x_test.values

            ##   small datasets processing #####
            if i == 0:
                if self.pipeline:
                    # Train model and use it in a pipeline to train on the fold  ##
                    pipe = Pipeline(
                        steps=[("preprocessor", preprocessor), ("model", self.base_estimator)])
                    self.base_estimator = rand_search(pipe, x_train, y_train, 
                                            model_name, self.pipeline, verbose=self.verbose)
                else:
                    ### leave the base estimator as is ###
                    self.base_estimator = rand_search(self.base_estimator, x_train, 
                                        y_train, model_name, self.pipeline, verbose=self.verbose)

                est_list = num_splits*[self.base_estimator]
                #print('    base estimator = %s' %self.base_estimator)
            
            ### SMOTE processing #####
            if i == 0:
                if smote:
                    print('Performing SMOTE...')
                    if self.verbose:
                        print('    x_train shape before SMOTE = %s' %(x_train.shape,))
                    
            if smote:
                # Get the class distribution for perfoming relative sampling in the next line
                ### It does not appear that class weights work well in SMOTE - hence avoid ###
                #class_weighted_rows = get_class_distribution(y_train, verbose)
                
                try:
                    if number_of_classes <= 1:
                        sm = ADASYN(n_neighbors=5, random_state=seed, )
                                    #sampling_strategy=class_weighted_rows)
                    else:
                        sm = SMOTETomek(random_state=42)
                        #sm = SMOTE(k_neighbors=5, random_state=seed,)
                                    #sampling_strategy=class_weighted_rows)
                    
                    x_train, y_train = sm.fit_resample(x_train, y_train)
                    if i == 0:
                        print('    x_train shape after SMOTE = %s' %(x_train.shape,))
                except:
                    if number_of_classes <= 1:
                        sm = SMOTETomek(random_state=42,)
                        #sm = ADASYN(n_neighbors=2, random_state=seed, )
                                    #sampling_strategy=class_weighted_rows)
                    else:
                        sm = SMOTE(k_neighbors=2, random_state=seed,)
                                    #sampling_strategy=class_weighted_rows)
                    
                    x_train, y_train = sm.fit_resample(x_train, y_train)                    
                    if i == 0 and smote:
                        print('    x_train shape after SMOTE = %s' %(x_train.shape,))
            
            # Initialize model with your supervised algorithm of choice
            model = est_list[i]
            
            model.fit(x_train, y_train)
            self.models.append(model)

            # Predict on remaining data of each fold
            preds = model.predict(x_test)

            # Use best classification metric to measure performance of model
            score = balanced_accuracy_score(y_test, preds)
            print("    Fold %s: OOF Score: %0.0f%%" %(i+1, 100*score))
            self.scores.append(score)

        # Compute average score
        averageAccuracy = sum(self.scores)/len(self.scores)
        if self.verbose:
            print("Average Balanced Accuracy of %s-model SuloClassifier: %0.0f%%" %(self.n_estimators, 100*averageAccuracy))


        # Finally, check out the total time taken
        end = time.time()
        timeTaken = end-start
        print("Time Taken: %0.0f (seconds)" %timeTaken)
        return self

    def predict(self, X):
        from scipy import stats
        weights = self.scores
        if self.multi_label:
            ### In multi-label, targets have to be numeric, so you can leave weights as-is ##
            ypre = np.array([model.predict(X) for model in self.models ])
            y_predis = np.average(ypre, axis=0, weights=weights)
            y_preds = np.round(y_predis,0).astype(int)
            return y_preds
        y_predis = np.column_stack([model.predict(X) for model in self.models ])
        ### This weights the model's predictions according to OOB scores obtained
        #### In single label, targets can be object or string, so weights cannot be applied always ##
        if y_predis.dtype == object or y_predis.dtype == bool:
            ### in the case of predictions that are strings, no need for weights ##
            y_predis = stats.mode(y_predis, axis=1)[0].ravel()
        else:
            if str(y_predis.dtype) == 'category':
                y_predis = stats.mode(y_predis, axis=1)[0].ravel()
            else:
                y_predis = np.average(y_predis, weights=weights, axis=1)
                y_predis = np.round(y_predis,0).astype(int)
        return y_predis
        #return stats.mode(y_predis,axis=1)[0].ravel()
    
    def predict_proba(self, X):
        weights = self.scores
        y_probas = [model.predict_proba(X) for model in self.models ]
        y_probas = return_predict_proba(y_probas)
        return y_probas

from sklearn.model_selection import StratifiedKFold, KFold
from sklearn.model_selection import RandomizedSearchCV
def rand_search(model, X, y, model_name, pipe_flag=False, verbose=0):
    start = time.time()
    if pipe_flag:
        model_string = 'model__'
    else:
        model_string = ''
    if model_name == 'rf':
        criterion = ["gini", "entropy", "log_loss"]
        # Number of trees in random forest
        n_estimators = [int(x) for x in np.linspace(start = 50, stop = 300, num = 10)]
        # Number of features to consider at every split
        max_features = ['auto', 'sqrt', 'log']
        # Maximum number of levels in tree
        max_depth = [2, 4, 6, 10, None]
        # Minimum number of samples required to split a node
        min_samples_split = [2, 5, 10]
        # Minimum number of samples required at each leaf node
        min_samples_leaf = [1, 2, 4]
        # Method of selecting samples for training each tree
        bootstrap = [True, False]
        ###  These are the RandomForest params ########        
        params = {
            model_string+'criterion': criterion,
            model_string+'n_estimators': n_estimators,
            #model_string+'max_features': max_features,
            #model_string+'max_depth': max_depth,
            #model_string+'min_samples_split': min_samples_split,
            #model_string+'min_samples_leaf': min_samples_leaf,
           model_string+'bootstrap': bootstrap,
                       }
    if model_name == 'bg':
        criterion = ["gini", "entropy", "log_loss"]
        # Number of trees in random forest
        n_estimators = [int(x) for x in np.linspace(start = 50, stop = 300, num = 10)]
        # Number of features to consider at every split
        #max_features = ['auto', 'sqrt', 'log']
        max_features = [0.3, 0.5, 0.7]
        # Maximum number of levels in tree
        max_depth = [2, 4, 6, 10, None]
        # Minimum number of samples required to split a node
        min_samples_split = [2, 5, 10]
        # Minimum number of samples required at each leaf node
        min_samples_leaf = [1, 2, 4]
        # Method of selecting samples for training each tree
        bootstrap = [True, False]
        ###  These are the RandomForest params ########
        params = {
            #model_string+'criterion': criterion,
            model_string+'n_estimators': n_estimators,
            #model_string+'max_features': max_features,
            #model_string+'max_depth': max_depth,
            #model_string+'min_samples_split': min_samples_split,
            #model_string+'min_samples_leaf': min_samples_leaf,
            model_string+'bootstrap': bootstrap,
            model_string+'bootstrap_features': bootstrap,
                       }
    elif model_name == 'lgb':
        # Number of estimators in LGBM Classifier ##
        n_estimators = np.linspace(50, 500, 10, dtype = "int")
        ### number of leaves is only for LGBM ###
        num_leaves = np.linspace(5, 500, 50, dtype = "int")
        ## learning rate is very important for LGBM ##
        learning_rate = sp.stats.uniform(scale=1)
        params = {
            model_string+'n_estimators': n_estimators,
            model_string+'num_leaves': num_leaves,
            model_string+'learning_rate': learning_rate,
                    }
    elif model_name == 'other':
        params =  {
            model_string+'gamma': [2, 4, 10, 20, 32],
            model_string+'kernel': ['knn', 'rbf'],
            model_string+'max_iter': [500, 1000, 2000],
            model_string+'n_neighbors': [2, 3, 5, 7],
                }
    else:
        ### Since we don't know what model will be sent, we cannot tune it ##
        params = {}
        return model

    kfold = StratifiedKFold(n_splits=5, random_state=100, shuffle=True)
    if verbose:
        print("Finding best params for base estimator using random search...")
    clf = RandomizedSearchCV(model, params, n_iter=4, scoring='balanced_accuracy',
                         cv = kfold, n_jobs=-1, random_state=100)
    
    clf.fit(X, y)

    if verbose:
        print("    best score is :" , clf.best_score_)
        #print("    best estimator is :" , clf.best_estimator_)
        print("    best Params is :" , clf.best_params_)
        print("Time Taken for random search: %0.0f (seconds)" %(time.time()-start))
    return clf.best_estimator_
##################################################################################
# Calculate class weight
from sklearn.utils.class_weight import compute_class_weight
import copy
from collections import Counter
def find_rare_class(classes, verbose=0):
    ######### Print the % count of each class in a Target variable  #####
    """
    Works on Multi Class too. Prints class percentages count of target variable.
    It returns the name of the Rare class (the one with the minimum class member count).
    This can also be helpful in using it as pos_label in Binary and Multi Class problems.
    """
    counts = OrderedDict(Counter(classes))
    total = sum(counts.values())
    if verbose >= 1:
        print('       Class  -> Counts -> Percent')
        sorted_keys = sorted(counts.keys())
        for cls in sorted_keys:
            print("%12s: % 7d  ->  % 5.1f%%" % (cls, counts[cls], counts[cls]/total*100))
    if type(pd.Series(counts).idxmin())==str:
        return pd.Series(counts).idxmin()
    else:
        return int(pd.Series(counts).idxmin())
##################################################################################
from collections import OrderedDict    
def get_class_weights(y_input, verbose=0):    
    y_input = copy.deepcopy(y_input)
    if isinstance(y_input, np.ndarray):
        y_input = pd.Series(y_input)
    elif isinstance(y_input, pd.Series):
        pass
    elif isinstance(y_input, pd.DataFrame):
        if len(y_input.columns) >= 2:
            ### if it is a dataframe, return only if it is one column dataframe ##
            class_weights = dict()
            for each_target in y_input.columns:
                class_weights[each_target] = get_class_weights(y_input[each_target])
            return class_weights
        else:
            y_input = y_input.values.reshape(-1)
    else:
        ### if you cannot detect the type or if it is a multi-column dataframe, ignore it
        return None
    classes = np.unique(y_input)
    rare_class = find_rare_class(y_input)
    xp = Counter(y_input)
    class_weights = compute_class_weight('balanced', classes=classes, y=y_input)
    class_weights = OrderedDict(zip(classes, np.round(class_weights/class_weights.min()).astype(int)))
    if verbose:
        print('Class weights used in classifier are: %s' %class_weights)
    return class_weights

from collections import OrderedDict
def get_scale_pos_weight(y_input, verbose=0):
    class_weighted_rows = get_class_weights(y_input)
    if isinstance(y_input, np.ndarray):
        y_input = pd.Series(y_input)
    elif isinstance(y_input, pd.Series):
        pass
    elif isinstance(y_input, pd.DataFrame):
        if len(y_input.columns) >= 2:
            ### if it is a dataframe, return only if it is one column dataframe ##
            rare_class_weights = OrderedDict()
            for each_target in y_input.columns:
                rare_class_weights[each_target] = get_scale_pos_weight(y_input[each_target])
            return rare_class_weights
        else:
            y_input = y_input.values.reshape(-1)
    
    rare_class = find_rare_class(y_input)
    rare_class_weight = class_weighted_rows[rare_class]
    if verbose:
        print('    For class %s, weight = %s' %(rare_class, rare_class_weight))
    return rare_class_weight
##########################################################################
from collections import defaultdict
from collections import OrderedDict
def return_minority_samples(y, verbose=0):
    """
    #### Calculates the % count of each class in y and returns a 
    #### smaller set of y based on being 5% or less of dataset.
    It returns the small y as an array or dataframe as input y was.
    """
    import copy
    y = copy.deepcopy(y)
    if isinstance(y, np.ndarray):
        ls = pd.Series(y).value_counts()[(pd.Series(y).value_counts(1)<=0.05).values].index
        return y[pd.Series(y).isin(ls).values]
    else:
        if isinstance(y, pd.Series):
            ls = y.value_counts()[(y.value_counts(1)<=0.05).values].index
        else:
            y = y.iloc[:,0]
            ls = y.value_counts()[(y.value_counts(1)<=0.05).values].index
        return y[y.isin(ls)]

def num_classes(y, verbose=0):
    """
    ### Returns number of classes in y
    """
    import copy
    y = copy.deepcopy(y)
    if isinstance(y, np.ndarray):
        ls = pd.Series(y).nunique()
    else:
        if isinstance(y, pd.Series):
            ls = y.nunique()
        else:
            if len(y.columns) >= 2:
                ls = OrderedDict()
                for each_i in y.columns:
                    ls[each_i] = y[each_i].nunique()
                return ls
            else:
                ls = y.nunique()[0]
    return ls

def return_minority_classes(y, verbose=0):
    """
    #### Calculates the % count of each class in y and returns a 
    #### smaller set of y based on being 5% or less of dataset.
    It returns the list of classes that are <=5% classes.
    """
    import copy
    y = copy.deepcopy(y)
    if isinstance(y, np.ndarray):
        ls = pd.Series(y).value_counts()[(pd.Series(y).value_counts(1)<=0.05).values].index
    else:
        if isinstance(y, pd.Series):
            ls = y.value_counts()[(y.value_counts(1)<=0.05).values].index
        else:
            y = y.iloc[:,0]
            ls = y.value_counts()[(y.value_counts(1)<=0.05).values].index
    return ls
#################################################################################
def get_cardinality(X, cat_features):
    ## pick a limit for cardinal variables here ##
    cat_limit = 30
    mask = X[cat_features].nunique() > cat_limit
    high_cardinal_vars = cat_features[mask]
    low_cardinal_vars = cat_features[~mask]
    return low_cardinal_vars, high_cardinal_vars
################################################################################
def is_y_object(y):
    test1 = (y.dtypes.any()==object) | (y.dtypes.any()==bool)
    test2 = str(y.dtypes.any())=='category'
    return test1 | test2

def print_flatten_dict(dd, separator='_', prefix=''):
    ### this function is to flatten dict to print classes and their order ###
    ### One solution here: https://stackoverflow.com/questions/6027558/flatten-nested-dictionaries-compressing-keys
    ### I have modified it to make it work for me #################
    return { prefix + separator + str(k) if prefix else k : v
             for kk, vv in dd.items()
             for k, v in print_flatten_dict(vv, separator, kk).items()
             } if isinstance(dd, dict) else { prefix : dd }

def print_accuracy(target, y_test, y_preds, verbose=0):
    bal_scores = []
    from sklearn.metrics import balanced_accuracy_score, classification_report
    if isinstance(target, str): 
        bal_score = balanced_accuracy_score(y_test,y_preds)
        bal_scores.append(bal_score)
        if verbose:
            print('Bal accu %0.0f%%' %(100*bal_score))
            print(classification_report(y_test,y_preds))
    elif len(target) == 1:
        bal_score = balanced_accuracy_score(y_test,y_preds)
        bal_scores.append(bal_score)
        if verbose:
            print('Bal accu %0.0f%%' %(100*bal_score))
            print(classification_report(y_test,y_preds))
    else:
        for each_i, target_name in enumerate(target):
            bal_score = balanced_accuracy_score(y_test.values[:,each_i],y_preds[:,each_i])
            bal_scores.append(bal_score)
            if verbose:
                if each_i == 0:
                    print('For %s:' %target_name)
                print('    Bal accu %0.0f%%' %(100*bal_score))
                print(classification_report(y_test.values[:,each_i],y_preds[:,each_i]))
    return np.mean(bal_scores)
##########################################################################################
from collections import defaultdict
def return_predict_proba(y_probas):
    ### This is for detecting what-label what-class problems with probas ####
    problemtype = ""
    if isinstance(y_probas, list):
        ### y_probas is a list when y is multi-label. 
        if isinstance(y_probas[0], list):
            ##    1. If y is multi_label but has more than two classes, y_probas[0] is also a list ##
            problemtype = "multi_label_multi_class"
        else:
            initial = y_probas[0].shape[1]
            if np.all([x.shape[1]==initial for x in y_probas]):
                problemtype =  "multi_label_binary_class"
            else:
                problemtype = "multi_label_multi_class"
    else:
        problemtype = "single_label"
    #### This is for making multi-label multi-class predictions into a dictionary ##
    if problemtype == "multi_label_multi_class":
        probas_dict = defaultdict(list)
        ### Initialize the default dict #############
        for each_target in range(len(y_probas[0])):
            probas_dict[each_target] = []
        #### Now that it is is initialized, compile each class into its own list ###
        if isinstance(y_probas[0], list):
            for each_i in range(len(y_probas)):
                for each_j in range(len(y_probas[each_i])):
                    if y_probas[each_i][each_j].shape[1] > 2:
                        probas_dict[each_j].append(y_probas[each_i][each_j])
                    else:
                        probas_dict[each_j].append(y_probas[each_i][each_j][:,1])
            #### Once all of the probas have been put in a dictionary, now compute means ##
            for each_target in range(len(probas_dict)):
                probas_dict[each_target] = np.array(probas_dict[each_target]).mean(axis=0)
    elif problemtype == "multi_label_binary_class":
        initial = y_probas[0].shape[1]
        if np.all([x.shape[1]==initial for x in y_probas]):
            probas_dict = np.array(y_probas).mean(axis=0)
    return probas_dict   
###############################################################################################
from sklearn.metrics import roc_auc_score
import copy
from sklearn.metrics import balanced_accuracy_score, classification_report
import pdb
def print_sulo_accuracy(y_test, y_preds, y_probas=''):
    bal_scores = []
    ####### Once you have detected what problem it is, now print its scores #####
    if y_test.ndim <= 1: 
        ### This is a single label problem # we need to test for multiclass ##
        bal_score = balanced_accuracy_score(y_test,y_preds)
        print('Bal accu %0.0f%%' %(100*bal_score))
        if not isinstance(y_probas, str):
            if y_probas.ndim <= 1:
                print('ROC AUC = %0.2f' %roc_auc_score(y_test, y_probas[:,1]))
            else:
                print('Multi-class ROC AUC = %0.2f' %roc_auc_score(y_test, y_probas, multi_class="ovr"))
        bal_scores.append(bal_score)
        print(classification_report(y_test,y_preds))
    elif y_test.ndim >= 2:
        if y_test.shape[1] == 1:
            bal_score = balanced_accuracy_score(y_test,y_preds)
            bal_scores.append(bal_score)
            print('Bal accu %0.0f%%' %(100*bal_score))
            if not isinstance(y_probas, str):
                if y_probas.shape[1] > 2:
                    print('ROC AUC = %0.2f' %roc_auc_score(y_test, y_probas, multi_class="ovr"))
                else:
                    print('ROC AUC = %0.2f' %roc_auc_score(y_test, y_probas[:,1]))
            print(classification_report(y_test,y_preds))
        else:
            if isinstance(y_probas, str):
                ### This is for multi-label problems without probas ####
                for each_i in range(y_test.shape[1]):
                    bal_score = balanced_accuracy_score(y_test.values[:,each_i],y_preds[:,each_i])
                    bal_scores.append(bal_score)
                    print('    Bal accu %0.0f%%' %(100*bal_score))
            else:
                ##### This is only for multi_label_multi_class problems
                num_targets = y_test.shape[1]
                for each_i in range(num_targets):
                    if len(np.unique(y_test.values[:,each_i])) > 2:
                        ### This nan problem happens due to Label Propagation but can be fixed as follows ##
                        mat = y_probas[each_i]
                        if np.any(np.isnan(mat)):
                            mat = pd.DataFrame(mat).fillna(method='ffill').values
                            bal_score = roc_auc_score(y_test.values[:,each_i],mat,multi_class="ovr")
                        else:
                            bal_score = roc_auc_score(y_test.values[:,each_i],y_probas[each_i],multi_class="ovr")
                    else:
                        if isinstance(y_probas, dict):
                            if y_probas[each_i].ndim <= 1:
                                ## This is caused by Label Propagation hence you must probas like this ##
                                mat = y_probas[each_i]
                                if np.any(np.isnan(mat)):
                                    mat = pd.DataFrame(mat).fillna(method='ffill').values
                                bal_score = roc_auc_score(y_test.values[:,each_i],mat)
                            else:
                                bal_score = roc_auc_score(y_test.values[:,each_i],y_probas[each_i][:,1])
                        else:
                            if y_probas.shape[1] == num_targets:
                                ### This means Label Propagation was used which creates probas like this ##
                                bal_score = roc_auc_score(y_test.values[:,each_i],y_probas[:,each_i])
                            else:
                                ### This means regular sklearn classifiers which predict multi dim probas #
                                bal_score = roc_auc_score(y_test.values[:,each_i],y_probas[each_i])
                    print('Target number %s: ROC AUC score %0.0f%%' %(each_i+1,100*bal_score))
                    bal_scores.append(bal_score)
                    print(classification_report(y_test.values[:,each_i],y_preds[:,each_i]))
    final_score = np.mean(bal_scores)
    print("final average accuracy score = %0.2f" %final_score)
    return final_score
##############################################################################