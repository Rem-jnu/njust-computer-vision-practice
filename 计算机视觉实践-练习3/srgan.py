

from __future__ import print_function, division
from keras.layers import *
from keras.layers.advanced_activations import LeakyReLU
from keras.layers.convolutional import UpSampling2D, Conv2D
from keras.applications import VGG19
from keras.models import Model
from keras.optimizers import Adam
import datetime
import matplotlib.pyplot as plt
from data_loader import DataLoader
import numpy as np
import os
from skimage.metrics import peak_signal_noise_ratio as psnr
from skimage.metrics import structural_similarity as ssim


class SRGAN():
    def __init__(self):
        # Input shape
        self.channels = 3
        self.lr_height = 64  # Low resolution height
        self.lr_width = 64  # Low resolution width
        self.lr_shape = (self.lr_height, self.lr_width, self.channels)
        self.hr_height = self.lr_height * 4  # High resolution height
        self.hr_width = self.lr_width * 4  # High resolution width
        self.hr_shape = (self.hr_height, self.hr_width, self.channels)

        # Number of residual blocks in the generator
        self.n_residual_blocks = 16

        optimizer = Adam(0.0002, 0.5)

        # We use a pre-trained VGG19 model to extract image features from the high resolution
        # and the generated high resolution images and minimize the mse between them
        self.vgg = self.build_vgg()
        self.vgg.trainable = False
        self.vgg.summary()
        self.vgg.compile(loss='mse',
                         optimizer=optimizer,
                         metrics=['accuracy'])

        # Configure data loader
        self.dataset_name = 'D:/github_repos/Keras-GAN-master/datasets/img_align_celeba'
        self.data_loader = DataLoader(dataset_name=self.dataset_name,
                                      img_res=(self.hr_height, self.hr_width))

        # Calculate output shape of D (PatchGAN)
        patch = int(self.hr_height / 2 ** 4)
        self.disc_patch = (patch, patch, 1)

        # Number of filters in the first layer of G and D
        self.gf = 64
        self.df = 64

        # Build and compile the discriminator
        self.discriminator = self.build_discriminator()
        self.discriminator.compile(loss='mse',
                                   optimizer=optimizer,
                                   metrics=['accuracy'])
        self.discriminator.summary()

        # Build the generator
        self.generator = self.build_generator()

        # High res. and low res. images
        img_hr = Input(shape=self.hr_shape)
        img_lr = Input(shape=self.lr_shape)

        # Generate high res. version from low res.
        fake_hr = self.generator(img_lr)

        # Extract image features of the generated img
        fake_features = self.vgg(fake_hr)

        # For the combined model we will only train the generator
        self.discriminator.trainable = False

        # Discriminator determines validity of generated high res. images
        validity = self.discriminator(fake_hr)

        self.combined = Model([img_lr, img_hr], [validity, fake_features])
        self.combined.summary()
        self.combined.compile(loss=['binary_crossentropy', 'mse'],
                              loss_weights=[1e-3, 1],
                              optimizer=optimizer)

    def build_vgg(self):
        """
        Builds a pre-trained VGG19 model that outputs image features extracted at the
        third block of the model
        """
        print('start loading trianed weights of vgg...')
        vgg = VGG19(weights="imagenet")
        # Set outputs to outputs of last conv. layer in block 3
        # See architecture at: https://github.com/keras-team/keras/blob/master/keras/applications/vgg19.py
        print('loading completes')
        vgg.outputs = [vgg.layers[9].output]

        img = Input(shape=self.hr_shape)

        # Extract image features
        img_features = vgg(img)

        return Model(img, img_features)

    def build_generator(self):

        def residual_block(layer_input, filters):
            """Residual block described in paper"""
            d = Conv2D(filters, kernel_size=3, strides=1, padding='same')(layer_input)
            d = Activation('relu')(d)
            d = BatchNormalization(momentum=0.8)(d)
            d = Conv2D(filters, kernel_size=3, strides=1, padding='same')(d)
            d = BatchNormalization(momentum=0.8)(d)
            d = Add()([d, layer_input])
            return d

        def deconv2d(layer_input):
            """Layers used during upsampling"""
            u = UpSampling2D(size=2)(layer_input)
            u = Conv2D(256, kernel_size=3, strides=1, padding='same')(u)
            u = Activation('relu')(u)
            return u

        # Low resolution image input
        img_lr = Input(shape=self.lr_shape)

        # Pre-residual block
        c1 = Conv2D(64, kernel_size=9, strides=1, padding='same')(img_lr)
        c1 = Activation('relu')(c1)

        # Propogate through residual blocks
        r = residual_block(c1, self.gf)
        for _ in range(self.n_residual_blocks - 1):
            r = residual_block(r, self.gf)

        # Post-residual block
        c2 = Conv2D(64, kernel_size=3, strides=1, padding='same')(r)
        c2 = BatchNormalization(momentum=0.8)(c2)
        c2 = Add()([c2, c1])

        # Upsampling
        u1 = deconv2d(c2)
        u2 = deconv2d(u1)

        # Generate high resolution output
        gen_hr = Conv2D(self.channels, kernel_size=9, strides=1, padding='same', activation='tanh')(u2)

        return Model(img_lr, gen_hr)

    def build_discriminator(self):

        def d_block(layer_input, filters, strides=1, bn=True):
            """Discriminator layer"""
            d = Conv2D(filters, kernel_size=3, strides=strides, padding='same')(layer_input)
            d = LeakyReLU(alpha=0.2)(d)
            if bn:
                d = BatchNormalization(momentum=0.8)(d)
            return d

        # Input img
        d0 = Input(shape=self.hr_shape)

        d1 = d_block(d0, self.df, bn=False)
        d2 = d_block(d1, self.df, strides=2)
        d3 = d_block(d2, self.df * 2)
        d4 = d_block(d3, self.df * 2, strides=2)
        d5 = d_block(d4, self.df * 4)
        d6 = d_block(d5, self.df * 4, strides=2)
        d7 = d_block(d6, self.df * 8)
        d8 = d_block(d7, self.df * 8, strides=2)

        d9 = Dense(self.df * 16)(d8)
        d10 = LeakyReLU(alpha=0.2)(d9)
        validity = Dense(1, activation='sigmoid')(d10)

        return Model(d0, validity)

    def train(self, epochs, batch_size=1, sample_interval=5):
        start_time = datetime.datetime.now()

        for epoch in range(epochs):
            if epoch > 30:
                sample_interval = 10
            if epoch > 100:
                sample_interval = 50

            # ----------------------
            #  Train Discriminator
            # ----------------------

            # Sample images and their conditioning counterparts
            imgs_hr, imgs_lr = self.data_loader.load_data(batch_size)

            # From low res. image generate high res. version
            fake_hr = self.generator.predict(imgs_lr)

            valid = np.ones((batch_size,) + self.disc_patch)
            fake = np.zeros((batch_size,) + self.disc_patch)

            # Train the discriminators (original images = real / generated = Fake)
            d_loss_real = self.discriminator.train_on_batch(imgs_hr, valid)
            d_loss_fake = self.discriminator.train_on_batch(fake_hr, fake)
            d_loss = 0.5 * np.add(d_loss_real, d_loss_fake)

            # ------------------
            #  Train Generator
            # ------------------

            # Sample images and their conditioning counterparts
            imgs_hr, imgs_lr = self.data_loader.load_data(batch_size)

            # The generators want the discriminators to label the generated images as real
            valid = np.ones((batch_size,) + self.disc_patch)

            # Extract ground truth image features using pre-trained VGG19 model
            image_features = self.vgg.predict(imgs_hr)

            # Train the generators
            g_loss = self.combined.train_on_batch([imgs_lr, imgs_hr], [valid, image_features])

            elapsed_time = datetime.datetime.now() - start_time
            # Plot the progress
            print("%d time: %s" % (epoch, elapsed_time))

            # If at save interval => save generated image samples
            if epoch % sample_interval == 0:
                self.sample_images_new(epoch)
            if epoch % 500 == 0 and epoch > 1:
                self.generator.save_weights('./saved_model/' + str(epoch) + '.h5')

    def test_images(self, batch_size=1):
        imgs_hr, imgs_lr = self.data_loader.load_data(batch_size, is_pred=True)
        os.makedirs('saved_model/', exist_ok=True)
        self.generator.load_weights('./saved_model/' + str(2000) + '.h5')
        fake_hr = self.generator.predict(imgs_lr)
        r, c = imgs_hr.shape[0], 2
        imgs_lr = 0.5 * imgs_lr + 0.5
        fake_hr = 0.5 * fake_hr + 0.5
        imgs_hr = 0.5 * imgs_hr + 0.5

        # Save generated images and the high resolution originals
        titles = ['Low resolution input', 'Generated Super resolution']
        fig, axs = plt.subplots(r, c)
        for row in range(r):
            for col, image in enumerate([imgs_lr, fake_hr]):
                axs[row, col].imshow(image[row])
                axs[row, col].set_title(titles[col])
                axs[row, col].axis('off')
        fig.savefig("./result.png")
        plt.close()

        # Save low resolution and generated high resolution images to files
        for i, (lr_img, hr_img) in enumerate(zip(imgs_lr, fake_hr)):
            plt.imsave(f"./res/bicubic_downsampling_{i+1}.png", lr_img)
            plt.imsave(f"./res/gen_high_resolution_{i+1}.png", hr_img)

        # Calculate PSNR and SSIM
        psnr_values = []
        ssim_values = []
        for img_real, img_generated in zip(imgs_hr, fake_hr):
            # Convert images to uint8 format (0-255)
            img_real = ((img_real + 1) * 127.5).astype(np.uint8)
            img_generated = ((img_generated + 1) * 127.5).astype(np.uint8)

            # Calculate PSNR
            psnr_value = psnr(img_real, img_generated)
            psnr_values.append(psnr_value)

            # Calculate SSIM
            ssim_value = ssim(img_real, img_generated, multichannel=True)
            ssim_values.append(ssim_value)
        print(psnr_values)
        print(ssim_values)
        avg_psnr = np.mean(psnr_values)
        avg_ssim = np.mean(ssim_values)
        print(f'Average PSNR: {avg_psnr:.2f}')
        print(f'Average SSIM: {avg_ssim:.4f}')

        print('------结束--------')




    def sample_images_new(self, epoch):
        os.makedirs('images/', exist_ok=True)
        imgs_hr, imgs_lr = self.data_loader.load_data(batch_size=1, is_testing=True, is_pred=True)
        fake_hr = self.generator.predict(imgs_lr)

        imgs_lr = 0.5 * imgs_lr + 0.5
        fake_hr = 0.5 * fake_hr + 0.5
        imgs_hr = 0.5 * imgs_hr + 0.5
        r, c = imgs_hr.shape[0], 3
        titles = ['Generated  epoch: ' + str(epoch), 'Original', 'Low']
        fig, axs = plt.subplots(r, c)

        for row in range(r):
            for col, image in enumerate([fake_hr, imgs_hr, imgs_lr]):
                axs[row, col].imshow(image[row])
                axs[row, col].set_title(titles[col])
                axs[row, col].axis('off')

        fig.savefig("images/%d.png" % (epoch))
        plt.close()


if __name__ == '__main__':
    gan = SRGAN()
    # gan.train(epochs=3000, batch_size=10, sample_interval=2)
    gan.test_images()
