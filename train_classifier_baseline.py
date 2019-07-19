from __future__ import absolute_import

import argparse
import collections
import multiprocessing
import os
from datetime import datetime
from functools import partial

from catalyst.dl import SupervisedRunner, EarlyStoppingCallback
from catalyst.dl.callbacks import AccuracyCallback, MixupCallback
from catalyst.utils import load_checkpoint, unpack_checkpoint
from pytorch_toolbelt.utils import fs
from pytorch_toolbelt.utils.catalyst import ShowPolarBatchesCallback, \
    ConfusionMatrixCallback
from pytorch_toolbelt.utils.random import set_manual_seed, get_random_name
from pytorch_toolbelt.utils.torch_utils import maybe_cuda, count_parameters, \
    set_trainable

from retinopathy.lib.callbacks import CappaScoreCallback, NegativeMiningCallback
from retinopathy.lib.dataset import get_class_names, \
    get_datasets, get_dataloaders
from retinopathy.lib.factory import get_model, get_loss, get_optimizer, \
    get_optimizable_parameters, get_scheduler
from retinopathy.lib.visualization import draw_classification_predictions

from retinopathy.scripts.clean_checkpoint import clean_checkpoint


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--seed', type=int, default=42, help='Random seed')
    parser.add_argument('--fast', action='store_true')
    parser.add_argument('--mixup', action='store_true')
    parser.add_argument('--balance', action='store_true')
    parser.add_argument('--swa', action='store_true')
    parser.add_argument('--show', action='store_true')
    parser.add_argument('--use-idrid', action='store_true')
    parser.add_argument('--use-messidor', action='store_true')
    parser.add_argument('--use-aptos2015', action='store_true')
    parser.add_argument('-v', '--verbose', action='store_true')
    parser.add_argument('-acc', '--accumulation-steps', type=int, default=1, help='Number of batches to process')
    parser.add_argument('-dd', '--data-dir', type=str, default='data', help='Data directory')
    parser.add_argument('-m', '--model', type=str, default='cls_resnet18', help='')
    parser.add_argument('-b', '--batch-size', type=int, default=8, help='Batch Size during training, e.g. -b 64')
    parser.add_argument('-e', '--epochs', type=int, default=100, help='Epoch to run')
    parser.add_argument('-es', '--early-stopping', type=int, default=None,
                        help='Maximum number of epochs without improvement')
    parser.add_argument('-f', '--fold', action='append', type=int, default=None)
    parser.add_argument('-fe', '--freeze-encoder', action='store_true')
    parser.add_argument('-lr', '--learning-rate', type=float, default=1e-4, help='Initial learning rate')
    parser.add_argument('-l', '--criterion', type=str, default='ce', help='Criterion')
    parser.add_argument('-o', '--optimizer', default='Adam', help='Name of the optimizer')
    parser.add_argument('-c', '--checkpoint', type=str, default=None,
                        help='Checkpoint filename to use as initial model weights')
    parser.add_argument('-w', '--workers', default=multiprocessing.cpu_count(), type=int, help='Num workers')
    parser.add_argument('-a', '--augmentations', default='medium', type=str, help='')
    parser.add_argument('-tta', '--tta', default=None, type=str, help='Type of TTA to use [fliplr, d4]')
    parser.add_argument('--transfer', default=None, type=str, help='')
    parser.add_argument('--fp16', action='store_true')
    parser.add_argument('-s', '--scheduler', default='multistep', type=str, help='')
    parser.add_argument('-wd', '--weight-decay', default=0, type=float, help='L2 weight decay')

    args = parser.parse_args()

    data_dir = args.data_dir
    num_workers = args.workers
    num_epochs = args.epochs
    batch_size = args.batch_size
    learning_rate = args.learning_rate
    early_stopping = args.early_stopping
    model_name = args.model
    optimizer_name = args.optimizer
    image_size = (512, 512)
    fast = args.fast
    augmentations = args.augmentations
    fp16 = args.fp16
    freeze_encoder = args.freeze_encoder
    criterion_name = args.criterion
    folds = args.fold
    mixup = args.mixup
    balance = args.balance
    use_swa = args.swa
    show_batches = args.show
    scheduler_name = args.scheduler
    verbose = args.verbose
    weight_decay = args.weight_decay
    use_idrid = args.use_idrid
    use_messidor = args.use_messidor
    use_aptos2015 = args.use_aptos2015

    current_time = datetime.now().strftime('%b%d_%H_%M')

    if folds is None or len(folds) == 0:
        folds = [None]

    for fold in folds:
        checkpoint_prefix = f'{model_name}_{get_random_name()}_fold{fold}'
        if use_aptos2015:
            checkpoint_prefix += '_aptos2015'
        if use_messidor:
            checkpoint_prefix += '_messidor'
        if use_idrid:
            checkpoint_prefix += '_idrid'

        set_manual_seed(args.seed)
        model = maybe_cuda(
            get_model(model_name, num_classes=len(get_class_names())))

        if args.transfer:
            transfer_checkpoint = fs.auto_file(args.transfer)
            print("Transfering weights from model checkpoint",
                  transfer_checkpoint)
            checkpoint = load_checkpoint(transfer_checkpoint)
            pretrained_dict = checkpoint['model_state_dict']

            for name, value in pretrained_dict.items():
                try:
                    model.load_state_dict(
                        collections.OrderedDict([(name, value)]), strict=False)
                except Exception as e:
                    print(e)

        checkpoint = None
        if args.checkpoint:
            checkpoint = load_checkpoint(fs.auto_file(args.checkpoint))
            unpack_checkpoint(checkpoint, model=model)

            checkpoint_epoch = checkpoint['epoch']
            print('Loaded model weights from:', args.checkpoint)
            print('Epoch                    :', checkpoint_epoch)
            print('Metrics (Train):',
                  'cappa:',
                  checkpoint['epoch_metrics']['train']['kappa_score'],
                  'accuracy01:',
                  checkpoint['epoch_metrics']['train']['accuracy01'],
                  'loss:', checkpoint['epoch_metrics']['train']['loss'])
            print('Metrics (Valid):',
                  'cappa:',
                  checkpoint['epoch_metrics']['valid']['kappa_score'],
                  'accuracy01:',
                  checkpoint['epoch_metrics']['valid']['accuracy01'],
                  'loss:', checkpoint['epoch_metrics']['valid']['loss'])

        if freeze_encoder:
            set_trainable(model.encoder, trainable=False, freeze_bn=True)

        criterion = get_loss(criterion_name)
        parameters = get_optimizable_parameters(model)
        optimizer = get_optimizer(optimizer_name, parameters,
                                  learning_rate=learning_rate,
                                  weight_decay=weight_decay)

        if checkpoint is not None:
            try:
                unpack_checkpoint(checkpoint, optimizer=optimizer)
                print('Restored optimizer state from checkpoint')
            except Exception as e:
                print('Failed to restore optimizer state from checkpoint', e)

        train_ds, valid_ds = get_datasets(data_dir=data_dir,
                                          use_aptos2019=True,
                                          use_aptos2015=use_aptos2015,
                                          use_idrid=use_idrid,
                                          use_messidor=use_messidor,
                                          image_size=image_size,
                                          augmentation=augmentations,
                                          target_dtype=int,
                                          fold=fold,
                                          folds=4)

        not_using_extra_data = not (use_idrid and use_messidor and use_aptos2015)
        train_loader, valid_loader = get_dataloaders(train_ds, valid_ds,
                                                     batch_size=batch_size,
                                                     num_workers=num_workers,
                                                     oversample_factor=2 if not_using_extra_data else 1,
                                                     balance=balance)

        if use_swa:
            from torchcontrib.optim import SWA
            optimizer = SWA(optimizer,
                            swa_start=len(train_loader),
                            swa_freq=512)

        loaders = collections.OrderedDict()
        loaders["train"] = train_loader
        loaders["valid"] = valid_loader

        prefix = f'classification/{model_name}/{checkpoint_prefix}'

        log_dir = os.path.join('runs', prefix)
        os.makedirs(log_dir, exist_ok=False)

        scheduler = get_scheduler(scheduler_name, optimizer,
                                  lr=learning_rate,
                                  num_epochs=num_epochs,
                                  batches_in_epoch=len(train_loader))

        print('Datasets         :', data_dir)
        print('  Train size     :', len(train_loader), len(train_loader.dataset))
        print('  Valid size     :', len(valid_loader), len(valid_loader.dataset))
        print('  Aptos 2019     :', True)
        print('  Aptos 2015     :', use_aptos2015)
        print('  IDRID          :', use_idrid)
        print('  Messidor       :', use_messidor)
        print('Train session    :', prefix)
        print('  FP16 mode      :', fp16)
        print('  Fast mode      :', fast)
        print('  Mixup          :', mixup)
        print('  Balance        :', balance)
        print('  Epochs         :', num_epochs)
        print('  Workers        :', num_workers)
        print('  Fold           :', fold)
        print('  Log dir        :', log_dir)
        print('  Augmentations  :', augmentations)
        print('Model            :', model_name)
        print('  Parameters     :', count_parameters(model))
        print('  Image size     :', image_size)
        print('  Freeze encoder :', freeze_encoder)
        print('Optimizer        :', optimizer_name)
        print('  Learning rate  :', learning_rate)
        print('  Batch size     :', batch_size)
        print('  Criterion      :', criterion_name)
        print('  Scheduler      :', scheduler_name)
        print('  Weight decay   :', weight_decay)
        print('  Early stopping :', early_stopping)

        # model training
        visualization_fn = partial(draw_classification_predictions,
                                   class_names=get_class_names())

        callbacks = [
            AccuracyCallback(),
            CappaScoreCallback(),
            ConfusionMatrixCallback(class_names=get_class_names()),
            NegativeMiningCallback()
        ]

        if mixup:
            callbacks += [MixupCallback(fields=['image'])]

        if early_stopping:
            callbacks += [
                EarlyStoppingCallback(early_stopping, metric='kappa_score',
                                      minimize=False)]

        if show_batches:
            callbacks += [
                ShowPolarBatchesCallback(visualization_fn, metric='accuracy01',
                                         minimize=False)]

        runner = SupervisedRunner(input_key='image')
        runner.train(
            fp16=fp16,
            model=model,
            criterion=criterion,
            optimizer=optimizer,
            scheduler=scheduler,
            callbacks=callbacks,
            loaders=loaders,
            logdir=log_dir,
            num_epochs=num_epochs,
            verbose=verbose,
            main_metric='kappa_score',
            minimize_metric=False,
            checkpoint_data={"cmd_args": vars(args)}
        )

        del runner, callbacks, loaders, optimizer, model, criterion, scheduler

        best__checkpoint = os.path.join(log_dir, 'checkpoints', 'best.pth')
        model_checkpoint = os.path.join(log_dir, 'checkpoints', f'{checkpoint_prefix}_best.pth')
        clean_checkpoint(best__checkpoint, model_checkpoint)


if __name__ == '__main__':
    main()
