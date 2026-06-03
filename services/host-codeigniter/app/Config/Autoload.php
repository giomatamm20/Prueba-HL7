<?php

namespace Config;

use CodeIgniter\Config\AutoloadConfig;

class Autoload extends AutoloadConfig
{
    public $psr4 = [
        APP_NAMESPACE => APPPATH,
    ];

    public $files = [];
    public $helpers = [];
}
