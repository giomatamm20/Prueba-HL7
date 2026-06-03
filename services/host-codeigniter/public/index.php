<?php

define('FCPATH', __DIR__ . DIRECTORY_SEPARATOR);

require FCPATH . '../app/Config/Paths.php';

$paths = new Config\Paths();
require rtrim($paths->systemDirectory, '\\/ ') . DIRECTORY_SEPARATOR . 'Boot.php';

exit(CodeIgniter\Boot::bootWeb($paths));
