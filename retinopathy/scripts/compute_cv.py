import torch
import numpy as np

#
# checkpoints = [
#     'runs/classification/cls_resnet18/fold_0/Jul08_14_51_ce/checkpoints/fold0_best.pth',
#     'runs/classification/cls_resnet18/fold_1/Jul08_16_13_ce/checkpoints/fold1_best.pth',
#     'runs/classification/cls_resnet18/fold_2/Jul09_00_19_ce/checkpoints/fold2_best.pth',
#     'runs/classification/cls_resnet18/fold_3/Jul09_01_29_ce/checkpoints/fold3_best.pth'
# ]

# checkpoints = [
#     'runs/regression/reg_resnet18/fold_3/Jul09_18_20_clipped_mse/checkpoints/fold3_best.pth',
#     'runs/regression/reg_resnet18/fold_2/Jul09_16_32_clipped_mse/checkpoints/fold2_best.pth',
#     'runs/regression/reg_resnet18/fold_1/Jul09_14_57_clipped_mse/checkpoints/fold1_best.pth',
#     'runs/regression/reg_resnet18/fold_0/Jul09_13_44_clipped_mse/checkpoints/fold0_best.pth'
# ]

checkpoints = [
    'runs/classification/cls_resnext50_gap/fold_3/Jul18_08_57_ce_fp16/checkpoints/best.pth',
    'runs/classification/cls_resnext50_gap/fold_2/Jul18_03_48_ce_fp16/checkpoints/best.pth',
    'runs/classification/cls_resnext50_gap/fold_1/Jul18_00_54_ce_fp16/checkpoints/best.pth',
    'runs/classification/cls_resnext50_gap/fold_0/Jul17_22_37_ce_fp16/checkpoints/best.pth'
]
cv = []

for checkpoint in checkpoints:
    checkpoint = torch.load(checkpoint)
    cv.append(checkpoint['valid_metrics']['kappa_score'])

print(np.mean(cv))
print(np.std(cv))
