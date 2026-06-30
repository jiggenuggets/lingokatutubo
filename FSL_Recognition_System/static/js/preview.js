// Shows a live preview of the selected hand sign photo before the user submits it.
// Also does a quick client-side check on file type/size (the server re-validates
// everything regardless, since client-side checks can always be bypassed).

document.addEventListener('DOMContentLoaded', function () {
    var imageInput = document.getElementById('id_image');
    var previewBox = document.getElementById('preview-box');
    var previewImg = document.getElementById('image-preview');
    var form = document.getElementById('upload-form');

    if (!imageInput) {
        return;
    }

    var ALLOWED_TYPES = ['image/jpeg', 'image/jpg', 'image/png'];
    var MAX_SIZE_BYTES = 5 * 1024 * 1024; // 5MB, matches the server-side limit

    imageInput.addEventListener('change', function () {
        var file = imageInput.files[0];

        if (!file) {
            previewBox.hidden = true;
            return;
        }

        if (ALLOWED_TYPES.indexOf(file.type) === -1) {
            alert('Please choose a JPG, JPEG, or PNG image.');
            imageInput.value = '';
            previewBox.hidden = true;
            return;
        }

        if (file.size > MAX_SIZE_BYTES) {
            alert('Image is too large. Maximum allowed size is 5MB.');
            imageInput.value = '';
            previewBox.hidden = true;
            return;
        }

        var reader = new FileReader();
        reader.onload = function (event) {
            previewImg.src = event.target.result;
            previewBox.hidden = false;
        };
        reader.readAsDataURL(file);
    });

    if (form) {
        form.addEventListener('submit', function (event) {
            if (!imageInput.files[0]) {
                event.preventDefault();
                alert('Please choose a photo before submitting.');
            }
        });
    }
});
