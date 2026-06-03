<?php

namespace App\Controllers;

use App\Models\CrmStore;
use CodeIgniter\HTTP\ResponseInterface;
use CodeIgniter\RESTful\ResourceController;

class ApiController extends ResourceController
{
    private CrmStore $store;

    public function __construct()
    {
        $this->store = new CrmStore();
        $this->store->initialize();
    }

    public function status(): ResponseInterface
    {
        return $this->respond([
            'listener' => $this->store->listenerSnapshot(),
            'database' => $this->store->statusSummary(),
        ]);
    }

    public function messages(): ResponseInterface
    {
        $limit = $this->boundedLimit((int) ($this->request->getGet('limit') ?? 25), 1, 200);
        return $this->respond($this->store->messages($limit));
    }

    public function patients(): ResponseInterface
    {
        return $this->respond($this->store->patients());
    }

    public function orders(): ResponseInterface
    {
        return $this->respond($this->store->orders());
    }

    public function results(): ResponseInterface
    {
        $limit = $this->boundedLimit((int) ($this->request->getGet('limit') ?? 100), 1, 500);
        return $this->respond($this->store->results($limit));
    }

    private function boundedLimit(int $value, int $minimum, int $maximum): int
    {
        return max($minimum, min($maximum, $value));
    }
}
