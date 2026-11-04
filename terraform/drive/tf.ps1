# Lance terraform sur le stockage du Drive avec les secrets du magasin global (setup.py) :  .\tf.ps1 plan   /   .\tf.ps1 apply
python "$PSScriptRoot\..\..\setup.py" tf drive @args
