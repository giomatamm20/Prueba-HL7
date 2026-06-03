<?php

namespace App\Controllers;

use CodeIgniter\Controller;

class DashboardController extends Controller
{
    public function index(): string
    {
        return view('dashboard');
    }
}
