# Projet de cours « Nuages de points et modélisation 3D »
Ce projet s'inscrit dans le cadre du cours « Nuages de points et modélisation 3D » donné par le centre de robotique (CAOR) de MINES ParisTech dans les masters 2 IASD et MVA des universités PSL et Paris-Saclay.

## Introduction
Le traitement automatisé de nuages de points en trois dimensions présente un double enjeu : d'une part, le champ applicatif des méthodes de segmentation et de classification ne cesse d\'augmenter avec le développement des systèmes de navigation automatiques, de suivi d\'infrastructures, ... D'autre part, l\'amélioration de la qualité des données va avec une augmentation de leur taille et du temps de calcul associé. Il devient donc très utile de concevoir des méthodes fiables et performantes pour analyser les données issues de nuages de points 3D.
La méthode proposée dans l\'article [1] propose d\'agréger les données 3D en voxels sur lesquels sera ensuite effectué le traitement de classification.
Le but de ce projet est de mettre en œuvre, d\'analyser et de proposer une approche critique de cette méthode pour la mettre en contexte avec les données et les réalités applicatives.

## Premiers pas et utilisation
Le fichier bin/tests.py peut servir à faire ses premiers pas avec les outils que nous avons développés. Il ne permet toutefois pas de faire de classification. Pour ceci, vous pouvez utiliser le fichier bin/pipeline.py pour exécuter la séquence complète d'apprentissage et de classification de différents nuages, en adaptant les paramètres en fonction de vos souhaits et des données présentes sur votre machine.
Les données sont à stocker dans le sous-répertoire data/

## Rapport
Le rapport du projet est disponible [ici](docs/rapport-09042020.pdf).

## License
Le code source de ce projet est distribué sous licence MIT. Consultez le fichier LICENSE pour plus d'informations.

## Références

[1] Segmentation Based Classification of 3D Urban Point Clouds: A Super-Voxel Based Approach with Evaluation, Ahmad Kamal Aijazi, Paul Checchin, and Laurent Trassoudaine. Remote Sensing 2013

## Structure
La structure du projet est la suivante
|Répertoire|Contenu|
|-|-|
|bin| Scripts |
|data| Jeux de données utilisés (non synchronisés)|
|docs| Documentation et rapport|
|src| Code source |