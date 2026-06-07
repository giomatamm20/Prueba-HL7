<?php

namespace Config;

use CodeIgniter\Config\View as BaseView;

class View extends BaseView
{
    public $saveData = true;
    public $filters = [];
    public $plugins = [];
    public array $decorators = [];
    public string $appOverridesFolder = 'overrides';
}
